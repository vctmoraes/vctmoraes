#!/usr/bin/env python3
"""Teste de fumaça do Deck S21. Roda em qualquer sistema, sem Mac.

O teste cria comandos falsos do macOS (osascript, sips, pmset...), sobe o servidor
com eles, faz o pareamento e confere as rotas, os cartões de informação e todos os
botões da configuração.

Uso:   python3 tests/smoke.py
Saída: uma linha por teste. Código de saída 0 se tudo passou.

O que este teste NÃO prova: o AppleScript compilar no macOS, as permissões do
macOS e o compilador Swift. Esses pontos só o Mac confirma.
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = []


# ------------------------------------------------------------------ ferramentas

def png(size=32, rgb=(120, 60, 200)):
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print("%s  %s%s" % ("OK   " if condition else "FALHA", name, ("  -> " + str(detail)) if detail and not condition else ""))
    return bool(condition)


def write_exec(path, text):
    path.write_text(text)
    path.chmod(0o755)


def make_fake_macos(bindir, home, art_file, log):
    """Comandos falsos do macOS. Cada um registra o que recebeu em 'log'."""
    bindir.mkdir(parents=True)
    art = str(art_file)
    write_exec(bindir / "osascript", """#!/bin/bash
echo "osascript $*" | head -c 300 >> "%(log)s"; echo >> "%(log)s"
if [ "$1" = "-l" ]; then cp "%(art)s" "${@: -1}"; exit 0; fi
script="$2"
case "$script" in
  *"input volume of s"*) echo "60|40|false";;
  *"player state is playing"*) printf 'playing\\tBohemian Rhapsody\\tQueen\\tA Night at the Opera\\t83\\t355\\n';;
  *"player state as text"*) printf 'playing\\tBohemian Rhapsody\\tQueen\\n';;
  *"raw data"*) p=$(echo "$script" | grep -o 'POSIX file "[^"]*"' | sed 's/POSIX file "//; s/"$//'); cp "%(art)s" "$p";;
  *"display dialog"*) echo "projeto de teste";;
esac
""" % {"log": log, "art": art})
    write_exec(bindir / "sips", """#!/bin/bash
in=""; out=""; while [ $# -gt 0 ]; do case "$1" in --out) out="$2";; *.icns|*.img) in="$1";; esac; shift; done
cp "$in" "$out"
""")
    write_exec(bindir / "pgrep", "#!/bin/bash\nexit 0\n")
    write_exec(bindir / "ipconfig", "#!/bin/bash\n[ \"$2\" = en0 ] && echo 192.168.0.15\n")
    write_exec(bindir / "pmset", """#!/bin/bash
if [ "$1" = "-g" ]; then
  echo "Now drawing from 'AC Power'"
  echo " -InternalBattery-0 (id=1)	87%%; charging; 0:42 remaining present: true"
fi
echo "pmset $*" >> "%s"
""" % log)
    for name in ("open", "shortcuts", "pbcopy"):
        write_exec(bindir / name, "#!/bin/bash\necho \"%s $*\" >> \"%s\"\ncat > /dev/null 2>&1 < /dev/stdin || true\n" % (name, log))
    write_exec(bindir / "top", "#!/bin/bash\necho 'CPU usage: 30.0% user, 10.0% sys, 60.0% idle'\necho 'CPU usage: 12.5% user, 8.0% sys, 79.5% idle '\n")
    write_exec(bindir / "sysctl", "#!/bin/bash\necho 17179869184\n")
    write_exec(bindir / "vm_stat", """#!/bin/bash
cat <<X
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages purgeable:                          5000.
Pages wired down:                       100000.
Anonymous pages:                        250000.
Pages occupied by compressor:           100000.
X
""")
    write_exec(bindir / "swiftc", """#!/bin/bash
out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
case "$out" in
 *rem-list) printf '#!/bin/bash\\nn=$(date +%%s)\\nprintf "%%s\\\\t0\\\\tEntregar relatorio\\\\n" $((n-86400))\\nprintf "0\\\\t0\\\\tComprar cafe\\\\n"\\n' > "$out";;
 *) printf '#!/bin/bash\\nn=$(date +%%s)\\nprintf "%%s\\\\t%%s\\\\t0\\\\tReuniao de equipe\\\\n" $((n+1500)) $((n+5100))\\n' > "$out";;
esac
chmod +x "$out"
""")
    # app falso, com ícone, para testar a conversão de ícones
    bundle = home / "Applications" / "Alpha.app" / "Contents"
    (bundle / "Resources").mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(
        b'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIconFile</key><string>AppIcon</string></dict></plist>')
    (bundle / "Resources" / "AppIcon.icns").write_bytes(art_file.read_bytes())
    # sessões do Claude Code falsas
    proj = home / ".claude" / "projects" / "-Users-teste-projeto-alfa"
    proj.mkdir(parents=True)
    now = time.time()
    from datetime import datetime, timezone
    lines = []
    for i in range(6):
        t = datetime.fromtimestamp(now - 3000 + i * 400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        lines.append(json.dumps({"type": "assistant", "cwd": "/Users/teste/projeto-alfa", "timestamp": t, "requestId": "r%d" % i,
                                 "message": {"id": "m%d" % i, "usage": {"input_tokens": 10, "output_tokens": 90,
                                                                         "cache_creation_input_tokens": 400}}}))
    (proj / "sessao.jsonl").write_text("\n".join(lines) + "\n")


class FakeWeb(BaseHTTPRequestHandler):
    """Open-Meteo e busca do iTunes falsos."""
    art = b""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/geo"):
            body, kind = {"results": [{"name": "Cidade Teste", "latitude": -25.4, "longitude": -49.2}]}, "application/json"
        elif self.path.startswith("/fc"):
            body, kind = {"current": {"temperature_2m": 21.4, "apparent_temperature": 22.0, "weather_code": 2},
                          "daily": {"temperature_2m_max": [25.0], "temperature_2m_min": [14.0],
                                    "precipitation_probability_max": [30]}}, "application/json"
        elif self.path.startswith("/search"):
            body, kind = {"results": [{"artworkUrl100": "http://127.0.0.1:%d/100x100bb.jpg" % self.server.server_port}]}, "application/json"
        else:
            body, kind = FakeWeb.art, "image/jpeg"
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Client:
    def __init__(self, port):
        self.base = "http://127.0.0.1:%d" % port
        self.token = ""

    def call(self, path, body=None, token=True, method=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method or ("POST" if body is not None else "GET"))
        if token and self.token:
            req.add_header("X-Deck-Token", self.token)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                kind = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if "json" in kind else raw), kind
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw), ""
            except ValueError:
                return e.code, raw, ""


# ------------------------------------------------------------------------ teste

def main():
    tmp = Path(tempfile.mkdtemp(prefix="deck-smoke-"))
    server = None
    web = None
    try:
        deck = tmp / "deck"
        shutil.copytree(ROOT, deck, ignore=shutil.ignore_patterns(".git", ".token", ".state.json", ".cache", ".icons",
                                                                   "__pycache__", "backgrounds", "deck.log", "config.local.json"))
        home, bindir, log = tmp / "home", tmp / "bin", tmp / "calls.log"
        art_file = tmp / "art.png"
        art_file.write_bytes(png(48))
        log.write_text("")
        make_fake_macos(bindir, home, art_file, log)
        FakeWeb.art = art_file.read_bytes()
        web = HTTPServer(("127.0.0.1", 0), FakeWeb)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        wp, port = web.server_port, free_port()
        (deck / "config.local.json").write_text(json.dumps({
            "port": port,
            "info": {"weather": {"city": "Cidade Teste", "geocode_url": "http://127.0.0.1:%d/geo" % wp,
                                 "forecast_url": "http://127.0.0.1:%d/fc" % wp},
                     "music": {"itunes_url": "http://127.0.0.1:%d/search" % wp}},
            "extra_pages": [{"name": "Teste", "buttons": [
                {"label": "Alpha", "symbol": "play", "action": {"type": "app", "name": "Alpha"}}]}]}))
        env = dict(os.environ, HOME=str(home), PATH=str(bindir) + os.pathsep + os.environ["PATH"], FAKE_LOG=str(log))

        # 1. verificação da configuração
        r = subprocess.run([sys.executable, "-I", str(deck / "server.py"), "--check"], capture_output=True, text=True, env=env)
        check("config: python3 server.py --check passa", r.returncode == 0, r.stdout[-400:])

        # 2. sobe o servidor
        server = subprocess.Popen([sys.executable, "-I", "-u", str(deck / "server.py")], cwd=deck, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        output, pin = [], None
        deadline = time.time() + 15
        while time.time() < deadline and pin is None:
            line = server.stdout.readline()
            if not line:
                break
            output.append(line)
            if line.strip().isdigit() and len(line.strip()) == 6:
                pin = line.strip()
        check("servidor: mostra endereço do Wi-Fi e código de pareamento", pin and "192.168.0.15" in "".join(output), "".join(output)[-300:])
        if not pin:
            return finish()
        threading.Thread(target=lambda: [output.append(l) for l in server.stdout], daemon=True).start()
        c = Client(port)

        # 3. segurança e pareamento
        check("rotas: página inicial abre sem token", c.call("/", token=False)[0] == 200)
        check("rotas: /api/config sem token dá 401", c.call("/api/config", token=False)[0] == 401)
        check("rotas: /api/run sem token dá 401", c.call("/api/run", {"id": "0-0"}, token=False)[0] == 401)
        check("pareamento: código errado é recusado", c.call("/api/pair", {"pin": "000000" if pin != "000000" else "111111"}, token=False)[0] == 403)
        code, data, _ = c.call("/api/pair", {"pin": pin}, token=False)
        check("pareamento: código certo devolve o token", code == 200 and len(data.get("token", "")) >= 20, data)
        c.token = data.get("token", "")

        # 4. configuração pública
        code, cfg, _ = c.call("/api/config")
        pages = cfg.get("pages", []) if isinstance(cfg, dict) else []
        check("config: a lista pública não leva comandos (action)", code == 200 and "action" not in json.dumps(cfg))
        check("config: páginas pessoais (extra_pages) entram", any(p["name"] == "Teste" for p in pages))

        # 5. cartões de informação
        wanted = "weather,system,claude_sessions,claude_window,calendar,music,reminders"
        info, deadline = {}, time.time() + 30
        while time.time() < deadline:
            info = c.call("/api/info?s=" + wanted)[1]
            if isinstance(info, dict) and info and not any(v["loading"] for v in info.values()):
                break
            time.sleep(1)
        for name in wanted.split(","):
            sec = info.get(name, {}) if isinstance(info, dict) else {}
            check("info: cartão '%s' tem dados" % name, sec.get("data") is not None, sec.get("error") or sec)
        d = lambda k: (info.get(k) or {}).get("data") or {}
        check("info: clima vem do serviço falso", d("weather").get("temp") == 21)
        check("info: memória calculada (6,8 de 16 GB)", d("system").get("mem_used") == 6.8 and d("system").get("mem_total") == 16)
        check("info: bateria e disco", (d("system").get("battery") or {}).get("pct") == 87 and d("system").get("disk_total", 0) > 0)
        check("info: sessão do Claude Code aparece", any(r["project"] == "projeto-alfa" for r in (d("claude_sessions") or [])))
        check("info: janela de 5 h conta as mensagens", d("claude_window").get("messages") == 6)
        check("info: música tocando", d("music").get("title") == "Bohemian Rhapsody")
        art = d("music").get("art")
        check("info: capa gerada", bool(art))
        code, body, kind = c.call("/api/art?k=%s" % art) if art else (0, b"", "")
        check("capa: /api/art entrega uma imagem", code == 200 and kind.startswith("image/"), (code, kind))
        check("capa: chave inválida dá 404", c.call("/api/art?k=../../etc/passwd")[0] == 404)
        check("info: lembrete atrasado marcado", any(i["overdue"] for i in d("reminders").get("items", [])))

        # 6. ícones
        check("ícones: ícone de linha abre", c.call("/icons/play.svg", token=False)[0] == 200)
        check("ícones: caminho fora da pasta dá 404", c.call("/icons/..%2fserver.py", token=False)[0] == 404)
        alpha = next((b["id"] for p in pages for b in p["buttons"] if b["label"] == "Alpha"), None)
        check("ícones: app falso vira PNG", c.call("/api/icon?app=Alpha")[0] == 200)
        check("ícones: app fora da configuração dá 404", c.call("/api/icon?app=Terminal")[0] == 404)
        check("fundo: nome fora da pasta dá 404", c.call("/api/bg?f=../config.json")[0] == 404)

        # 7. todos os botões
        has_zsh = Path("/bin/zsh").exists()
        total = failed = 0
        for page in pages:
            for btn in page["buttons"]:
                src = next(b for p in json.load(open(deck / "config.json", encoding="utf-8"))["pages"] for b in p.get("buttons", [])
                           if b.get("label") == btn["label"]) if btn["label"] != "Alpha" else {"action": {"type": "app"}}
                if src["action"]["type"] == "shell" and not has_zsh:
                    continue  # o macOS tem /bin/zsh; este sistema não
                total += 1
                code, data, _ = c.call("/api/run", {"id": btn["id"]})
                if code != 200:
                    failed += 1
                    check("botão '%s' (%s)" % (btn["label"], page["name"]), False, data)
        check("botões: %d botões executam sem erro" % total, failed == 0)
        calls = log.read_text()
        check("botões: teclas e comandos chegaram aos comandos falsos",
              "keystroke" in calls and "shortcuts" not in calls.split("\n")[0] or "keystroke" in calls)

        # 8. bloqueio do pareamento (por último: o código trava o servidor)
        for _ in range(6):
            code, data, _ = c.call("/api/pair", {"pin": "999999" if pin != "999999" else "888888"}, token=False)
        check("pareamento: depois de 5 erros o servidor bloqueia", code == 403 and "tentativas" in json.dumps(data), data)
        code, data, _ = c.call("/api/pair", {"pin": pin}, token=False)
        check("pareamento: bloqueado até com o código certo", code == 403, data)
        check("servidor continua vivo", server.poll() is None)
    finally:
        finish_cleanup(server, web, tmp)
    return finish()


def finish_cleanup(server, web, tmp):
    if server and server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    if web:
        web.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)


def finish():
    bad = [r for r in RESULTS if not r[1]]
    print("\n%d de %d testes passaram." % (len(RESULTS) - len(bad), len(RESULTS)))
    if bad:
        print("Falharam:")
        for name, _, detail in bad:
            print(" - " + name)
    else:
        print("Este teste não cobre o AppleScript, as permissões e o Swift. Esses só o Mac confirma.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
