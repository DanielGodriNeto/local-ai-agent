#!/usr/bin/env bash
# Type a request in the agent's text box. Hyprland: bind = SUPER, A, exec, ~/.config/local-ai-agent/text_trigger.sh
cd "$(dirname "$0")" && py=.venv/bin/python && { [ -x "$py" ] || py=python; } && exec "$py" agentd.py --type
