#!/usr/bin/env python3
"""Deck S21: transforma um celular em painel de botões para o macOS.

Roda no Mac. O celular abre a página em http://IP-DO-MAC:8787 e toca nos botões.
Só usa a biblioteca padrão do Python 3 (sem pip).
"""
import hmac
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
LOCAL_CONFIG_PATH = BASE / "config.local.json"
TOKEN_PATH = BASE / ".token"
STATE_PATH = BASE / ".state.json"
INDEX_PATH = BASE / "web" / "index.html"
ICON_DIR = BASE / ".icons"
APP_DIRS = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
            "/Applications/Utilities", "/System/Library/CoreServices", "~/Applications"]
APP_NAME_RE = re.compile(r"^[\w .+&()\-]+$")

LOCK = threading.Lock()


class ActionError(Exception):
    """Erro esperado: a mensagem aparece no celular."""


# ---------------------------------------------------------------- utilidades

def run(cmd, timeout=15, stdin=None, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin, env=env)
    except FileNotFoundError:
        raise ActionError("Comando '%s' não existe. Este servidor funciona só no macOS." % cmd[0])
    except subprocess.TimeoutExpired:
        raise ActionError("A ação demorou demais.")
    if p.returncode != 0:
        raise ActionError((p.stderr or p.stdout).strip()[:300] or "Erro %d" % p.returncode)
    return p.stdout.strip()


def osa(script, timeout=15):
    return run(["osascript", "-e", script], timeout)


def q(text):
    """Escapa texto para uma string do AppleScript."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def need(action, key):
    value = action.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ActionError("A ação precisa do campo '%s'." % key)
    return value


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state))


# ---------------------------------------------------------------------- ações

def act_app(a, cfg):
    name = need(a, "name")
    run(["open", "-a", name])
    return "Abrindo " + name


def act_url(a, cfg):
    url = need(a, "url")
    if not re.match(r"^https?://", url):
        raise ActionError("A URL precisa começar com http:// ou https://.")
    run(["open", url])
    return "Abrindo página"


def act_folder(a, cfg):
    path = Path(need(a, "path")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    run(["open", str(path)])
    return "Abrindo pasta"


def act_shortcut(a, cfg):
    name = need(a, "name")
    run(["shortcuts", "run", name], timeout=60)
    return "Atalho executado"


def act_applescript(a, cfg):
    return osa(need(a, "script"), timeout=60) or "Pronto"


def act_shell(a, cfg):
    return run(["/bin/zsh", "-c", need(a, "command")], timeout=60) or "Pronto"


def act_keys(a, cfg):
    mods = a.get("modifiers", [])
    allowed = ("command", "shift", "option", "control")
    if not isinstance(mods, list) or any(m not in allowed for m in mods):
        raise ActionError("Modificadores válidos: " + ", ".join(allowed))
    using = ""
    if mods:
        using = " using {%s}" % ", ".join(m + " down" for m in mods)
    if "code" in a:
        body = "key code %d" % int(a["code"])
    else:
        key = need(a, "key")
        if len(key) != 1:
            raise ActionError("O campo 'key' aceita uma única tecla.")
        body = "keystroke " + q(key)
    if a.get("focus"):
        run(["open", "-a", a["focus"]])
        time.sleep(0.4)
    times = max(1, min(int(a.get("repeat", 1)), 16))
    if times == 1:
        osa('tell application "System Events" to ' + body + using)
    else:
        osa('tell application "System Events"\nrepeat %d times\n%s%s\ndelay 0.05\nend repeat\nend tell'
            % (times, body, using))
    return "Tecla enviada"


def act_paste(a, cfg):
    text = need(a, "text")
    if len(text) > 20000:
        raise ActionError("Texto longo demais (máximo 20000 caracteres).")
    # sem LANG em UTF-8, o pbcopy estraga acentos quando o servidor roda pelo launchd
    run(["pbcopy"], stdin=text, env=dict(os.environ, LANG="en_US.UTF-8"))
    act_keys({"key": "v", "modifiers": ["command"], "focus": a.get("focus")}, cfg)
    return "Texto colado"


MUSIC_CMDS = {"playpause": "playpause", "next": "next track", "previous": "previous track"}


def act_music(a, cfg):
    player = cfg.get("player", "Spotify")
    if player not in ("Spotify", "Music"):
        raise ActionError("O campo 'player' aceita Spotify ou Music.")
    cmd = MUSIC_CMDS.get(a.get("cmd"))
    if not cmd:
        raise ActionError("Comandos de música: " + ", ".join(MUSIC_CMDS))
    osa('tell application "%s" to %s' % (player, cmd))
    return "OK"


def read_volumes():
    out = osa(
        "set s to get volume settings\n"
        'return ((input volume of s) as text) & "|" & ((output volume of s) as text)'
        ' & "|" & ((output muted of s) as text)'
    )
    mic, vol, muted = (out.split("|") + ["", "", ""])[:3]

    def to_int(v):
        try:
            return int(v)
        except ValueError:
            return None

    return {"mic": to_int(mic), "out": to_int(vol), "muted": muted == "true"}


def act_mic(a, cfg):
    mode = a.get("mode", "toggle")
    with LOCK:
        current = read_volumes()["mic"]
        if current is None:
            raise ActionError("Este microfone não permite controlar o volume de entrada.")
        state = load_state()
        if mode == "mute" or (mode == "toggle" and current > 0):
            if current > 0:
                state["mic_prev"] = current
                save_state(state)
            osa("set volume input volume 0")
            return "Microfone mutado"
        osa("set volume input volume %d" % state.get("mic_prev", 75))
        return "Microfone ativo"


def act_volume(a, cfg):
    if a.get("mute") == "toggle":
        osa("set volume output muted (not (output muted of (get volume settings)))")
        return "OK"
    if "set" in a:
        target = int(a["set"])
    elif "delta" in a:
        current = read_volumes()["out"]
        if current is None:
            raise ActionError("Esta saída de som não permite controlar o volume.")
        target = current + int(a["delta"])
    else:
        raise ActionError("Use 'set', 'delta' ou 'mute'.")
    target = max(0, min(100, target))
    osa("set volume output volume %d" % target)
    return "Volume %d" % target


def act_new_project(a, cfg):
    base = Path(a.get("base", "~/Projetos")).expanduser()
    try:
        raw = osa(
            "tell current application to activate\n"
            'text returned of (display dialog "Nome do novo projeto:" default answer ""'
            ' with title "Deck" buttons {"Cancelar", "Criar"} default button "Criar")',
            timeout=300,
        )
    except ActionError as e:
        if "-128" in str(e):
            return "Cancelado"
        raise
    slug = re.sub(r"[^\w\- ]", "", raw, flags=re.UNICODE).strip().replace(" ", "-")
    if not slug:
        raise ActionError("Nome de projeto vazio.")
    if a.get("date_prefix", True):
        slug = "%s-%s" % (date.today().isoformat(), slug)
    path = base / slug
    if path.exists():
        raise ActionError("A pasta já existe: " + slug)
    for sub in a.get("folders", []):
        if sub.startswith("/") or ".." in Path(sub).parts:
            raise ActionError("Subpasta inválida: " + sub)
    path.mkdir(parents=True)
    for sub in a.get("folders", []):
        (path / sub).mkdir(parents=True, exist_ok=True)
    if a.get("open_with"):
        run(["open", "-a", a["open_with"], str(path)])
    else:
        run(["open", str(path)])
    return "Projeto criado: " + slug


ACTIONS = {
    "app": act_app,
    "url": act_url,
    "folder": act_folder,
    "shortcut": act_shortcut,
    "applescript": act_applescript,
    "shell": act_shell,
    "keys": act_keys,
    "paste": act_paste,
    "music": act_music,
    "mic": act_mic,
    "volume": act_volume,
    "new_project": act_new_project,
}


# ------------------------------------------------------------------- estado

_cache = {"t": 0.0, "data": {}}
_cache_lock = threading.Lock()


def now_playing(player):
    if player not in ("Spotify", "Music"):
        return {"state": "off"}
    try:
        run(["pgrep", "-x", player], timeout=3)
    except ActionError:
        return {"state": "off"}
    out = osa(
        'tell application "%s"\n'
        "try\n"
        'return (player state as text) & tab & (name of current track) & tab & (artist of current track)\n'
        "on error\n"
        'return "stopped" & tab & "" & tab & ""\n'
        "end try\n"
        "end tell" % player
    )
    state, title, artist = (out.split("\t") + ["", ""])[:3]
    return {"state": state, "title": title, "artist": artist}


def get_status(cfg):
    with _cache_lock:
        if time.time() - _cache["t"] < 1.5:
            return _cache["data"]
        data = {}
        try:
            v = read_volumes()
            data["mic_muted"] = v["mic"] == 0
            data["volume"] = v["out"]
            data["sound_muted"] = v["muted"]
        except ActionError as e:
            data["error"] = str(e)
        try:
            data["player"] = now_playing(cfg.get("player", "Spotify"))
        except ActionError:
            data["player"] = {"state": "off"}
        _cache.update(t=time.time(), data=data)
        return data


# ------------------------------------------------------------------- ícones

# Plano B: pede o ícone ao próprio macOS (funciona também com ícones sem arquivo .icns).
JXA_ICON = """
ObjC.import('AppKit');
function run(argv) {
  var icon = $.NSWorkspace.sharedWorkspace.iconForFile(argv[0]);
  var dst = $.NSImage.alloc.initWithSize($.NSMakeSize(256, 256));
  dst.lockFocus;
  icon.drawInRectFromRectOperationFraction($.NSMakeRect(0, 0, 256, 256), $.NSMakeRect(0, 0, 0, 0), 2, 1.0);
  dst.unlockFocus;
  var rep = $.NSBitmapImageRep.imageRepWithData(dst.TIFFRepresentation);
  var png = rep.representationUsingTypeProperties(4, $());
  png.writeToFileAtomically(argv[1], true);
}
"""

_icon_lock = threading.Lock()
_icon_fail = {}


def icon_app_name(btn):
    """Nome do app cujo ícone o botão mostra, ou None."""
    name = btn.get("icon_app")
    if name is None and btn.get("action", {}).get("type") == "app":
        name = btn["action"].get("name")
    if isinstance(name, str) and APP_NAME_RE.match(name):
        return name
    return None


def find_app(name):
    for d in APP_DIRS:
        path = Path(d).expanduser() / (name + ".app")
        if path.is_dir():
            return path
    try:
        out = run(["mdfind", "kMDItemFSName == '%s.app'" % name], timeout=5)
    except ActionError:
        return None
    for line in out.splitlines():
        if Path(line).name == name + ".app" and Path(line).is_dir():
            return Path(line)
    return None


def icns_of(app):
    res = app / "Contents" / "Resources"
    try:
        with open(app / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        info = {}
    for key in ("CFBundleIconFile", "CFBundleIconName"):
        n = info.get(key)
        if isinstance(n, str) and n:
            for cand in (n, n + ".icns"):
                path = res / cand
                if path.suffix == ".icns" and path.is_file():
                    return path
    found = sorted(res.glob("*.icns"))
    return found[0] if found else None


def make_icon(name):
    ICON_DIR.mkdir(exist_ok=True)
    out = ICON_DIR / (re.sub(r"[^\w.-]", "_", name) + ".png")
    if out.is_file() and out.stat().st_size > 0:
        return out
    app = find_app(name)
    if not app:
        raise ActionError("app '%s' não encontrado" % name)
    errors = []
    try:
        icns = icns_of(app)
        if icns:
            run(["sips", "-s", "format", "png", "-Z", "256", str(icns), "--out", str(out)], timeout=10)
            if out.is_file() and out.stat().st_size > 0:
                return out
        else:
            errors.append("sem arquivo .icns")
    except (ActionError, OSError) as e:
        errors.append("sips: %s" % e)
    try:
        run(["osascript", "-l", "JavaScript", "-e", JXA_ICON, str(app), str(out)], timeout=20)
        if out.is_file() and out.stat().st_size > 0:
            return out
        errors.append("osascript não gerou o arquivo")
    except ActionError as e:
        errors.append("osascript: %s" % e)
    raise ActionError("; ".join(errors))


def get_icon(cfg, name):
    """Retorna o caminho do PNG do ícone de um app da configuração, ou None."""
    allowed = {icon_app_name(b) for page in cfg["pages"] for b in page.get("buttons", [])}
    if name not in allowed:
        return None
    with _icon_lock:
        failed = _icon_fail.get(name)
        if failed and time.time() - failed < 60:
            return None
        try:
            return make_icon(name)
        except ActionError as e:
            _icon_fail[name] = time.time()
            print("[ícone] %s: %s" % (name, e), flush=True)
            return None


# ------------------------------------------------------------------ informações

CACHE_DIR = BASE / ".cache"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WINDOW_HOURS = 5


class Section:
    """Dado que se atualiza em segundo plano. O pedido HTTP nunca espera por ele."""

    def __init__(self, name, ttl, fn):
        self.name, self.ttl, self.fn = name, ttl, fn
        self.lock = threading.Lock()
        self.t = 0.0
        self.key = None
        self.data = None
        self.error = None
        self.busy = False

    def get(self, cfg, key=None):
        with self.lock:
            if key != self.key:  # a configuração mudou
                self.key, self.t, self.data, self.error = key, 0.0, None, None
            if time.time() - self.t > self.ttl and not self.busy:
                self.busy = True
                threading.Thread(target=self._run, args=(cfg, key), daemon=True).start()
            return {"data": self.data, "error": self.error,
                    "loading": self.data is None and self.error is None}

    def _run(self, cfg, key):
        try:
            data, err = self.fn(cfg), None
        except Exception as e:  # qualquer falha vira mensagem na tela
            data, err = None, (str(e) or e.__class__.__name__)
        with self.lock:
            if key == self.key:
                if err is None:
                    self.data, self.error = data, None
                    self.t = time.time()
                else:
                    if err != self.error:
                        print("[info] %s: %s" % (self.name, err), flush=True)
                    self.error = err
                    self.t = time.time() - (self.ttl - min(self.ttl, 60))  # tenta de novo em 1 min
            self.busy = False


def info_cfg(cfg, name):
    value = cfg.get("info", {}).get(name, {})
    return value if isinstance(value, dict) else {}


# --- clima (Open-Meteo: gratuito, sem conta)

WMO = [
    (0, "Céu limpo", "☀️"), (1, "Quase limpo", "🌤️"), (2, "Parcialmente nublado", "⛅"),
    (3, "Nublado", "☁️"), (45, "Neblina", "🌫️"), (48, "Neblina", "🌫️"),
    (51, "Garoa", "🌦️"), (53, "Garoa", "🌦️"), (55, "Garoa forte", "🌦️"),
    (61, "Chuva fraca", "🌧️"), (63, "Chuva", "🌧️"), (65, "Chuva forte", "🌧️"),
    (71, "Neve", "🌨️"), (73, "Neve", "🌨️"), (75, "Neve forte", "🌨️"),
    (80, "Pancadas de chuva", "🌦️"), (81, "Pancadas de chuva", "🌦️"), (82, "Pancadas fortes", "⛈️"),
    (95, "Trovoadas", "⛈️"), (96, "Trovoadas com granizo", "⛈️"), (99, "Trovoadas com granizo", "⛈️"),
]
_geo_cache = {}


def wmo(code):
    best = WMO[0]
    for item in WMO:
        if item[0] <= code:
            best = item
    return best[1], best[2]


def http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "deck-s21"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def weather_key(cfg):
    w = info_cfg(cfg, "weather")
    return (w.get("city"), w.get("latitude"), w.get("longitude"))


def weather_fn(cfg):
    w = info_cfg(cfg, "weather")
    lat, lon, label = w.get("latitude"), w.get("longitude"), w.get("city", "")
    if lat is None or lon is None:
        if not label:
            raise ActionError("Defina a cidade no config.json (info, weather, city).")
        if label not in _geo_cache:
            g = http_json(w.get("geocode_url", GEO_URL) + "?" + urlencode(
                {"name": label, "count": 1, "language": "pt", "format": "json"}))
            found = (g.get("results") or [None])[0]
            if not found:
                raise ActionError("Cidade '%s' não encontrada." % label)
            _geo_cache[label] = (found["latitude"], found["longitude"], found.get("name", label))
        lat, lon, label = _geo_cache[label]
    f = http_json(w.get("forecast_url", FORECAST_URL) + "?" + urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max"}))
    cur, day = f["current"], f["daily"]
    text, icon = wmo(int(cur["weather_code"]))
    return {"city": label, "temp": round(cur["temperature_2m"]), "feels": round(cur["apparent_temperature"]),
            "text": text, "icon": icon, "max": round(day["temperature_2m_max"][0]),
            "min": round(day["temperature_2m_min"][0]), "rain": day["precipitation_probability_max"][0]}


# --- CPU e memória

def system_fn(cfg):
    out = run(["top", "-l", "2", "-n", "0", "-s", "1"], timeout=15)
    lines = [l for l in out.splitlines() if l.startswith("CPU usage")]
    m = re.search(r"([\d.]+)% idle", lines[-1]) if lines else None
    if not m:
        raise ActionError("Não consegui ler o uso de CPU.")
    cpu = max(0, min(100, round(100 - float(m.group(1)))))
    total = int(run(["sysctl", "-n", "hw.memsize"]))
    vm = run(["vm_stat"])
    ps = re.search(r"page size of (\d+) bytes", vm)
    page = int(ps.group(1)) if ps else 16384
    v = {k.strip(): int(n) for k, n in re.findall(r"^(.+?):\s+(\d+)\.", vm, re.M)}
    pages = (v.get("Anonymous pages", 0) - v.get("Pages purgeable", 0)
             + v.get("Pages wired down", 0) + v.get("Pages occupied by compressor", 0))
    used = max(0, pages * page)
    gib = 1024 ** 3
    disk = shutil.disk_usage(os.path.expanduser("~"))
    return {"cpu": cpu, "mem_pct": round(100 * used / total), "mem_used": round(used / gib, 1),
            "mem_total": round(total / gib), "battery": battery_info(),
            "disk_free": round(disk.free / 1e9), "disk_total": round(disk.total / 1e9)}


# --- Claude Code (só arquivos locais; nenhum token é lido)

def claude_dir(cfg):
    base = info_cfg(cfg, "claude").get("projects_dir")
    if not base:
        base = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"), "projects")
    return Path(base).expanduser()


def tail_records(path, size=262144):
    """Registros JSON do fim do arquivo, do mais novo para o mais antigo."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        start = max(0, end - size)
        f.seek(start)
        raw = f.read().decode("utf-8", errors="replace")
    lines = raw.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # a primeira linha pode estar cortada
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def claude_sessions(cfg):
    base = claude_dir(cfg)
    if not base.is_dir():
        raise ActionError("Pasta %s não existe. O Claude Code já rodou neste Mac?" % base)
    c = info_cfg(cfg, "claude")
    limit = float(c.get("active_minutes", 30)) * 60
    now = time.time()
    newest = {}
    for f in base.glob("*/*.jsonl"):
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        if age > limit:
            continue
        cwd = None
        try:
            for rec in tail_records(f):
                if rec.get("cwd"):
                    cwd = rec["cwd"]
                    break
        except OSError:
            continue
        name = Path(cwd).name if cwd else f.parent.name.rsplit("-", 1)[-1]
        if name not in newest or age < newest[name]:
            newest[name] = age
    rows = [{"project": n, "age": int(a), "working": a < 45} for n, a in newest.items()]
    rows.sort(key=lambda r: r["age"])
    return rows[:5]


def claude_window(cfg):
    """Janela de 5 h do Claude Code, estimada pelos registros locais (sem porcentagem)."""
    base = claude_dir(cfg)
    if not base.is_dir():
        raise ActionError("Pasta %s não existe." % base)
    now = time.time()
    horizon = now - (WINDOW_HOURS * 2 + 1) * 3600
    entries = {}
    for f in base.glob("*/*.jsonl"):
        try:
            if f.stat().st_mtime < horizon:
                continue
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                    msg = rec.get("message") or {}
                    usage, stamp = msg.get("usage"), rec.get("timestamp")
                    if not isinstance(usage, dict) or not stamp:
                        continue
                    t = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                    tokens = sum(int(usage.get(k) or 0) for k in
                                 ("input_tokens", "output_tokens", "cache_creation_input_tokens"))
                except (ValueError, AttributeError, TypeError):
                    continue
                if t < horizon:
                    continue
                key = (msg.get("id"), rec.get("requestId")) if msg.get("id") else (str(f), t)
                entries[key] = (t, tokens)
    start = end = None
    count = tokens = 0
    for t, n in sorted(entries.values()):
        if start is None or t >= end:
            start = t - (t % 3600)
            end = start + WINDOW_HOURS * 3600
            count = tokens = 0
        count += 1
        tokens += n
    if start is None or now >= end:
        return {"active": False}
    return {"active": True, "start": start, "end": end, "messages": count, "tokens": tokens}


# --- bateria e disco do Mac

def battery_info():
    try:
        out = run(["pmset", "-g", "batt"], timeout=5)
    except ActionError:
        return None
    m = re.search(r"(\d+)%;\s*([^;]+);", out)
    if not m:
        return None
    return {"pct": int(m.group(1)), "plugged": "AC Power" in out,
            "charging": m.group(2).strip() == "charging"}


# --- música tocando, com capa

MUSIC_AS = """tell application "Music"
set st to (player state as text)
if st is "stopped" then return "stopped"
try
set t to current track
return st & tab & (name of t) & tab & (artist of t) & tab & (album of t) & tab & ((round (player position)) as text) & tab & ((round (duration of t)) as text)
on error e
return "error" & tab & e
end try
end tell"""

SPOTIFY_AS = """tell application "Spotify"
set st to (player state as text)
if st is "stopped" then return "stopped"
try
set t to current track
return st & tab & (name of t) & tab & (artist of t) & tab & (album of t) & tab & ((round (player position)) as text) & tab & ((round ((duration of t) / 1000)) as text) & tab & (artwork url of t)
on error e
return "error" & tab & e
end try
end tell"""

ART_DIR = CACHE_DIR / "art"
ITUNES_URL = "https://itunes.apple.com/search"
_art_fail = {}


def art_key(album, artist, title):
    base = "%s|%s" % (album or title, artist)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def sniff_image(data):
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def download_bytes(url, timeout=8, limit=3_000_000):
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ActionError("endereço de capa inválido")
    req = urllib.request.Request(url, headers={"User-Agent": "deck-s21"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ActionError("capa grande demais")
    return data


def local_art(path):
    """Pede a capa ao app Música e grava no arquivo."""
    osa(
        'tell application "Music"\n'
        "set t to current track\n"
        'if (count of artworks of t) = 0 then error "sem capa"\n'
        "set d to raw data of artwork 1 of t\n"
        "end tell\n"
        "set f to open for access (POSIX file %s) with write permission\n"
        "try\n"
        "set eof f to 0\n"
        "write d to f\n"
        "close access f\n"
        "on error e\n"
        "try\n"
        "close access f\n"
        "end try\n"
        "error e\n"
        "end try" % q(str(path)), timeout=15)


def online_art(cfg, album, artist, title):
    m = info_cfg(cfg, "music")
    url = m.get("itunes_url", ITUNES_URL) + "?" + urlencode(
        {"term": ("%s %s" % (artist, album or title)).strip(), "entity": "album", "limit": 1})
    found = (http_json(url).get("results") or [None])[0]
    if not found or not found.get("artworkUrl100"):
        raise ActionError("capa não encontrada")
    return download_bytes(found["artworkUrl100"].replace("100x100bb", "600x600bb"))


def ensure_art(cfg, player, key, album, artist, title, arturl):
    ART_DIR.mkdir(parents=True, exist_ok=True)
    path = ART_DIR / (key + ".img")
    if path.is_file() and path.stat().st_size > 0:
        return True
    failed = _art_fail.get(key)
    if failed and time.time() - failed < 300:
        return False
    errors = []
    try:
        if player == "Music":
            try:
                local_art(path)
            except ActionError as e:
                errors.append(str(e))
        elif arturl:
            path.write_bytes(download_bytes(arturl))
        if (not path.is_file() or path.stat().st_size == 0) and info_cfg(cfg, "music").get("online_art", True):
            path.write_bytes(online_art(cfg, album, artist, title))
        if path.is_file() and path.stat().st_size > 0:
            kind = sniff_image(path.read_bytes()[:16])
            if kind == "image/tiff":  # o navegador não mostra TIFF
                tmp = path.with_suffix(".png")
                run(["sips", "-s", "format", "png", str(path), "--out", str(tmp)], timeout=10)
                tmp.replace(path)
            return True
    except (ActionError, OSError, ValueError) as e:
        errors.append(str(e))
    except Exception as e:  # rede fora do ar, JSON ruim etc.
        errors.append(str(e))
    try:
        path.unlink()
    except OSError:
        pass
    _art_fail[key] = time.time()
    print("[info] capa de '%s': %s" % (title, "; ".join(errors) or "sem capa"), flush=True)
    return False


def music_fn(cfg):
    player = cfg.get("player", "Spotify")
    if player not in ("Spotify", "Music"):
        raise ActionError("O campo 'player' aceita Spotify ou Music.")
    try:
        run(["pgrep", "-x", player], timeout=3)
    except ActionError:
        return {"state": "off"}
    out = osa(MUSIC_AS if player == "Music" else SPOTIFY_AS, timeout=10)
    f = (out.split("\t") + [""] * 7)[:7]
    state, title, artist, album, pos, dur, arturl = f
    if state == "error":
        raise ActionError("O app %s não entregou a faixa: %s" % (player, title))
    if state == "stopped" or not title:
        return {"state": "stopped"}
    try:
        pos, dur = int(pos), int(dur)
    except ValueError:
        pos = dur = 0
    key = art_key(album, artist, title)
    has_art = ensure_art(cfg, player, key, album, artist, title, arturl)
    return {"state": state, "title": title, "artist": artist, "album": album,
            "pos": pos, "dur": dur, "art": key if has_art else None}


# --- lembretes (EventKit, programa Swift compilado uma vez)

REM_SWIFT = r"""
import EventKit
import Foundation

let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false
let done: (Bool, Error?) -> Void = { ok, _ in granted = ok; sem.signal() }
if #available(macOS 14.0, *) {
    store.requestFullAccessToReminders(completion: done)
} else {
    store.requestAccess(to: .reminder, completion: done)
}
_ = sem.wait(timeout: .now() + 30)
guard granted else {
    FileHandle.standardError.write("Sem acesso aos Lembretes. Ajustes do Sistema, Privacidade e Seguranca, Lembretes.\n".data(using: .utf8)!)
    exit(2)
}
let cal = Calendar.current
let end = cal.date(byAdding: .day, value: 1, to: cal.startOfDay(for: Date()))!
let pred = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: end, calendars: nil)
let fetched = DispatchSemaphore(value: 0)
store.fetchReminders(matching: pred) { items in
    for r in items ?? [] {
        var due: Date? = nil
        var hasTime = 0
        if let c = r.dueDateComponents {
            due = cal.date(from: c)
            hasTime = (c.hour != nil) ? 1 : 0
        }
        let t = Int(due?.timeIntervalSince1970 ?? 0)
        let title = (r.title ?? "").replacingOccurrences(of: "\t", with: " ").replacingOccurrences(of: "\n", with: " ")
        print("\(t)\t\(hasTime)\t\(title)")
    }
    fetched.signal()
}
_ = fetched.wait(timeout: .now() + 30)
"""


def reminders_fn(cfg):
    src_dir = CACHE_DIR / "rem-src"
    exe = CACHE_DIR / "rem-list"
    if not exe.is_file():
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "main.swift").write_text(REM_SWIFT, encoding="utf-8")
        run(["swiftc", "-O", str(src_dir / "main.swift"), "-o", str(exe)], timeout=180)
    out = run([str(exe)], timeout=60)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    items = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            due, has_time = float(parts[0]), parts[1] == "1"
        except ValueError:
            continue
        items.append({"due": due or None, "has_time": has_time, "title": parts[2] or "(sem título)",
                      "overdue": bool(due) and due < today})
    items.sort(key=lambda i: (not i["overdue"], i["due"] is None, i["due"] or 0))
    return {"total": len(items), "items": items[:8]}


# --- agenda (EventKit, via um programinha em Swift compilado uma vez)

CAL_SWIFT = r"""
import EventKit
import Foundation

let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false
let done: (Bool, Error?) -> Void = { ok, _ in granted = ok; sem.signal() }
if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents(completion: done)
} else {
    store.requestAccess(to: .event, completion: done)
}
_ = sem.wait(timeout: .now() + 30)
guard granted else {
    FileHandle.standardError.write("Sem acesso ao Calendario. Ajustes do Sistema, Privacidade e Seguranca, Calendarios.\n".data(using: .utf8)!)
    exit(2)
}
let cal = Calendar.current
let dayStart = cal.startOfDay(for: Date())
let end = cal.date(byAdding: .day, value: 2, to: dayStart)!
let pred = store.predicateForEvents(withStart: dayStart, end: end, calendars: nil)
for e in store.events(matching: pred).sorted(by: { $0.startDate < $1.startDate }) {
    let title = (e.title ?? "").replacingOccurrences(of: "\t", with: " ").replacingOccurrences(of: "\n", with: " ")
    print("\(Int(e.startDate.timeIntervalSince1970))\t\(Int(e.endDate.timeIntervalSince1970))\t\(e.isAllDay ? 1 : 0)\t\(title)")
}
"""


def calendar_fn(cfg):
    CACHE_DIR.mkdir(exist_ok=True)
    src, exe = CACHE_DIR / "main.swift", CACHE_DIR / "cal-events"
    if not exe.is_file():
        src.write_text(CAL_SWIFT, encoding="utf-8")
        run(["swiftc", "-O", str(src), "-o", str(exe)], timeout=180)
    out = run([str(exe)], timeout=60)
    now = time.time()
    events = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            start, end, all_day = float(parts[0]), float(parts[1]), parts[2] == "1"
        except ValueError:
            continue
        if end > now:
            events.append({"start": start, "end": end, "all_day": all_day, "title": parts[3] or "(sem título)"})
    events.sort(key=lambda e: e["start"])
    return events[:6]


SECTIONS = {
    "weather": ("weather", Section("clima", 600, weather_fn)),
    "system": ("system", Section("sistema", 4, system_fn)),
    "claude_sessions": ("claude", Section("sessões do Claude", 5, claude_sessions)),
    "claude_window": ("claude", Section("janela do Claude", 60, claude_window)),
    "calendar": ("calendar", Section("agenda", 120, calendar_fn)),
    "music": ("music", Section("música", 3, music_fn)),
    "reminders": ("reminders", Section("lembretes", 120, reminders_fn)),
}


def get_info(cfg, wanted=None):
    out = {}
    for name, (group, section) in SECTIONS.items():
        if wanted is not None and name not in wanted:
            continue
        if info_cfg(cfg, group).get("enabled", True) is False:
            continue
        out[name] = section.get(cfg, weather_key(cfg) if name == "weather" else None)
    return out


# ------------------------------------------------------------------- config

class ConfigError(Exception):
    pass


def deep_merge(base, extra):
    """Mistura 'extra' em 'base'. Dicionários se fundem; o resto é substituído."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config():
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError("Erro ao ler config.json: %s" % e)
    if LOCAL_CONFIG_PATH.exists():  # ajustes pessoais, fora do Git
        try:
            deep_merge(cfg, json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise ConfigError("Erro ao ler config.local.json: %s" % e)
    extra = cfg.pop("extra_pages", [])  # páginas pessoais do config.local.json
    if isinstance(extra, list) and isinstance(cfg.get("pages"), list):
        cfg["pages"] = cfg["pages"] + extra
    pages = cfg.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ConfigError("config.json precisa de uma lista 'pages'.")
    for i, page in enumerate(pages):
        if page.get("type") not in (None, "buttons", "info"):
            raise ConfigError("Página '%s': tipo '%s' não existe." % (page.get("name", i), page.get("type")))
        for j, btn in enumerate(page.get("buttons", [])):
            btn["id"] = "%d-%d" % (i, j)
            kind = btn.get("action", {}).get("type")
            if kind not in ACTIONS:
                raise ConfigError(
                    "Botão '%s': tipo de ação '%s' não existe." % (btn.get("label", btn["id"]), kind)
                )
    return cfg


def public_config(cfg):
    """Envia ao celular só o visual. As ações ficam no Mac."""
    keep = ("id", "label", "icon", "icon_on", "color", "color_on", "indicator")
    def visual(b):
        out = {k: b[k] for k in keep if k in b}
        app = icon_app_name(b)
        if app:
            out["icon_app"] = app
        return out

    return {
        "pages": [
            {"name": p.get("name", "Página"), "type": p.get("type", "buttons"), "cards": p.get("cards"),
             "buttons": [visual(b) for b in p.get("buttons", [])]}
            for p in cfg["pages"]
        ]
    }


def find_button(cfg, button_id):
    for page in cfg["pages"]:
        for btn in page.get("buttons", []):
            if btn["id"] == button_id:
                return btn
    return None


# --------------------------------------------------------------------- HTTP

TOKEN = ""
PIN = ""
PIN_EXPIRES = 0.0
PIN_FAILS = 0
PIN_LOCK = threading.Lock()
PIN_TTL = 15 * 60
PIN_MAX_FAILS = 5


def check_pin(given):
    """Confere o código de pareamento. Retorna (ok, mensagem de erro)."""
    global PIN_FAILS
    with PIN_LOCK:
        if time.time() > PIN_EXPIRES:
            return False, "O código expirou. Reinicie o servidor no Mac."
        if PIN_FAILS >= PIN_MAX_FAILS:
            return False, "Muitas tentativas erradas. Reinicie o servidor no Mac."
        if hmac.compare_digest(str(given).encode(), PIN.encode()):
            return True, ""
        PIN_FAILS += 1
        return False, "Código errado."


class Handler(BaseHTTPRequestHandler):
    server_version = "DeckS21"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        given = self.headers.get("X-Deck-Token", "")
        return hmac.compare_digest(given.encode(), TOKEN.encode())

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = INDEX_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path not in ("/api/config", "/api/status", "/api/icon", "/api/info", "/api/art"):
            self.send_json(404, {"error": "Não encontrado"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "Token inválido"})
            return
        try:
            cfg = load_config()
        except ConfigError as e:
            self.send_json(500, {"error": str(e)})
            return
        if path == "/api/icon":
            names = parse_qs(urlparse(self.path).query).get("app", [""])
            icon = get_icon(cfg, names[0])
            if not icon:
                self.send_json(404, {"error": "Sem ícone"})
                return
            body = icon.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/art":
            key = parse_qs(urlparse(self.path).query).get("k", [""])[0]
            file = ART_DIR / (key + ".img")
            if not re.fullmatch(r"[0-9a-f]{40}", key) or not file.is_file():
                self.send_json(404, {"error": "Sem capa"})
                return
            body = file.read_bytes()
            kind = sniff_image(body[:16])
            if not kind or kind == "image/tiff":
                self.send_json(404, {"error": "Formato de capa não suportado"})
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/info":
            wanted = parse_qs(urlparse(self.path).query).get("s")
            self.send_json(200, get_info(cfg, set(wanted[0].split(",")) if wanted else None))
        elif path == "/api/config":
            self.send_json(200, public_config(cfg))
        else:
            self.send_json(200, get_status(cfg))

    def handle_pair(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 256:
                raise ValueError
            payload = json.loads(self.rfile.read(size) or b"{}")
        except ValueError:
            self.send_json(400, {"error": "Pedido inválido"})
            return
        pin = payload.get("pin", "") if isinstance(payload, dict) else ""
        ok, msg = check_pin(pin)
        if not ok:
            self.send_json(403, {"error": msg})
            return
        self.send_json(200, {"token": TOKEN})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/pair":
            self.handle_pair()
            return
        if path != "/api/run":
            self.send_json(404, {"error": "Não encontrado"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "Token inválido"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(size) or b"{}")
            cfg = load_config()
        except ConfigError as e:
            self.send_json(500, {"error": str(e)})
            return
        except ValueError:
            self.send_json(400, {"error": "Pedido inválido"})
            return
        btn = find_button(cfg, payload.get("id"))
        if not btn:
            self.send_json(404, {"error": "Botão não existe. Recarregue a página."})
            return
        action = btn["action"]
        try:
            msg = ACTIONS[action["type"]](action, cfg)
        except ActionError as e:
            self.send_json(500, {"error": str(e)})
            return
        except (ValueError, TypeError) as e:
            self.send_json(500, {"error": "Configuração inválida: %s" % e})
            return
        _cache["t"] = 0.0  # força atualizar o estado depois de uma ação
        self.send_json(200, {"ok": True, "message": msg})


def lan_ip():
    # No Mac, o Wi-Fi costuma ser en0. Isso evita mostrar o IP de uma VPN.
    for iface in ("en0", "en1"):
        try:
            ip = subprocess.run(["ipconfig", "getifaddr", iface],
                                capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            ip = ""
        if ip:
            return ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_token():
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text().strip()
    token = secrets.token_urlsafe(16)
    TOKEN_PATH.write_text(token)
    TOKEN_PATH.chmod(0o600)
    return token


def main():
    global TOKEN, PIN, PIN_EXPIRES
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(str(e))
    TOKEN = load_token()
    PIN = "%06d" % secrets.randbelow(10 ** 6)
    PIN_EXPIRES = time.time() + PIN_TTL
    host = cfg.get("host", "0.0.0.0")
    port = int(cfg.get("port", 8787))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    ip = lan_ip()
    print("Deck S21 rodando.\n")
    print("No celular, abra este endereço no Chrome:")
    print("   http://%s:%d/\n" % (ip, port))
    print("Digite este código de pareamento (vale por 15 minutos):")
    print("   %s\n" % PIN)
    print("Pare com Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nAté logo.")


if __name__ == "__main__":
    main()
