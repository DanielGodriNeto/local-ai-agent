#!/usr/bin/env bash
# Hold-to-talk. Hyprland: bind = SUPER, V, exec, ~/.config/local-ai-agent/voice_trigger.sh start
#                         bindr = SUPER, V, exec, ~/.config/local-ai-agent/voice_trigger.sh stop
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RUN="${XDG_RUNTIME_DIR:-/tmp}/local-ai-agent"
MODEL="${WHISPER_MODEL:-$HOME/.local/share/whisper/ggml-base.bin}"
WHISPER="${WHISPER_BIN:-whisper-cli}"
umask 077; mkdir -p "$RUN"

case "${1:-}" in
start)
  [ -f "$RUN/rec.pid" ] && exit 0
  pw-record --rate 16000 --channels 1 "$RUN/rec.wav" & echo $! > "$RUN/rec.pid"
  notify-send -t 1500 "Agente" "Ouvindo..."
  ;;
stop)
  [ -f "$RUN/rec.pid" ] || exit 0
  kill -INT "$(cat "$RUN/rec.pid")" 2>/dev/null || true; rm -f "$RUN/rec.pid"; sleep 0.3
  TEXT="$("$WHISPER" -m "$MODEL" -f "$RUN/rec.wav" -l pt -nt -np 2>/dev/null | tr -s '\n ' ' ' | sed 's/^ *//;s/ *$//')"
  rm -f "$RUN/rec.wav"
  [ -n "$TEXT" ] || { notify-send "Agente" "Nao ouvi nada"; exit 0; }
  notify-send -t 2500 "Agente ouviu" "$TEXT"
  OUT="$(AGENT_CONFIRM_CMD="$DIR/confirm_trigger.sh" python "$DIR/orchestrator.py" "$TEXT" 2>&1 | head -c 600)"
  notify-send "Agente" "$OUT"
  ;;
*) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
