#!/usr/bin/env sh
# One command: a private Python environment with the exact, hash-checked versions, then the first-run checks.
# Arch: sudo pacman -S python python-gobject at-spi2-core portaudio grim ydotool wtype   (then: systemctl --user enable --now ydotool)
# Debian/Ubuntu: python3-gi gir1.2-atspi-2.0 libportaudio2 libegl1 libxkbcommon0 grim ydotool wtype
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -c "import sys; sys.exit(sys.version_info < (3, 11))" || { echo "Precisa do Python 3.11 ou mais novo." >&2; exit 1; }
    python3 -m venv --system-site-packages .venv  # AT-SPI (python-gobject) only comes from the system
fi
.venv/bin/python -m pip install --disable-pip-version-check --require-hashes -r requirements.txt
.venv/bin/python onboarding.py
echo "Pronto. Atalho do Hyprland: bind = SUPER, V, exec, $(pwd)/voice_trigger.sh"
