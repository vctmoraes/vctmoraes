#!/usr/bin/env python3
"""Deck S21: transforma um celular em painel de botões para o macOS.

Roda no Mac. O celular abre a página em http://IP-DO-MAC:8787 e toca nos botões.
Só usa a biblioteca padrão do Python 3 (sem pip).
"""
import hmac
import json
import plistlib
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
TOKEN_PATH = BASE / ".token"
STATE_PATH = BASE / ".state.json"
INDEX_PATH = BASE / "web" / "index.html"
ICON_DIR = BASE / ".icons"
APP_DIRS = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
            "/Applications/Utilities", "~/Applications"]
APP_NAME_RE = re.compile(r"^[\w .+&()\-]+$")

LOCK = threading.Lock()


class ActionError(Exception):
    """Erro esperado: a mensagem aparece no celular."""


# ---------------------------------------------------------------- utilidades

def run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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
    osa('tell application "System Events" to ' + body + using)
    return "Tecla enviada"


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


def get_icon(cfg, button_id):
    """Retorna o caminho do PNG do ícone, ou None."""
    btn = find_button(cfg, button_id)
    name = icon_app_name(btn) if btn else None
    if not name:
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


# ------------------------------------------------------------------- config

class ConfigError(Exception):
    pass


def load_config():
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError("Erro ao ler config.json: %s" % e)
    pages = cfg.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ConfigError("config.json precisa de uma lista 'pages'.")
    for i, page in enumerate(pages):
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
            {"name": p.get("name", "Página"), "buttons": [visual(b) for b in p.get("buttons", [])]}
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
        if path not in ("/api/config", "/api/status", "/api/icon"):
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
            ids = parse_qs(urlparse(self.path).query).get("id", [""])
            icon = get_icon(cfg, ids[0])
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
