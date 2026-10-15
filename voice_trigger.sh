#!/usr/bin/env bash
# One press: overlay shows what you say live, silence ends it, then the agent acts.
# Hyprland:  bind = SUPER, V, exec, ~/.config/local-ai-agent/voice_trigger.sh
# Overlay window (windowrule): float, pin, noborder, noshadow for the "python" Qt window if it is tiled.
cd "$(dirname "$0")" && py=.venv/bin/python && { [ -x "$py" ] || py=python; } && exec "$py" agentd.py --toggle
