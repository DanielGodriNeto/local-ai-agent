#!/usr/bin/env bash
# Prints "s" if the user picks Sim. Arg 1 = question shown in the notification.
if command -v walker >/dev/null; then ASK=(walker --dmenu)
elif command -v fuzzel >/dev/null; then ASK=(fuzzel --dmenu)
else ASK=(rofi -dmenu -p confirmar); fi
notify-send "Agente pede confirmacao" "$1"
[ "$(printf 'Nao\nSim\n' | "${ASK[@]}")" = "Sim" ] && echo s || echo n
