#!/bin/bash
# Inicia o Deck S21 sozinho quando você entra no Mac.
# Uso: ./instalar-autostart.sh          (instala)
#      ./instalar-autostart.sh remover  (remove)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.deck-s21.plist"

if [ "$1" = "remover" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Autostart removido."
  exit 0
fi

PY="$(command -v python3)"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.deck-s21</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DIR/server.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/deck.log</string>
  <key>StandardErrorPath</key><string>$DIR/deck.log</string>
</dict>
</plist>
PLISTEOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Autostart instalado. O endereço do celular está em: $DIR/deck.log"
