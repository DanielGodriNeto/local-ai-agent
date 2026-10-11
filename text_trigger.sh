#!/usr/bin/env bash
# Type a request in a launcher box. Hyprland: bind = SUPER, A, exec, ~/.config/local-ai-agent/text_trigger.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v walker >/dev/null; then ASK=(walker --dmenu)
elif command -v fuzzel >/dev/null; then ASK=(fuzzel --dmenu)
else ASK=(rofi -dmenu -p agente); fi
TEXT="$("${ASK[@]}" </dev/null || true)"
[ -n "$TEXT" ] || exit 0
OUT="$(AGENT_CONFIRM_CMD="$DIR/confirm_trigger.sh" python "$DIR/orchestrator.py" "$TEXT" 2>&1 | head -c 600)"
notify-send "Agente" "$OUT"
