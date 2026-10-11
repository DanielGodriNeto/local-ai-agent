"""Windows global hotkey: Win+Alt+V starts a voice session (python hotkey_windows.py, or put it in shell:startup).

Win+V itself cannot be registered: Windows reserves it for clipboard history.
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
from pathlib import Path

MOD_ALT, MOD_WIN, MOD_NOREPEAT, WM_HOTKEY, VK_V = 0x1, 0x8, 0x4000, 0x312, 0x56
LISTEN = Path(__file__).parent / "listen.py"


def main() -> None:
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_V):
        sys.exit("Win+Alt+V is already taken by another program.")
    print("Win+Alt+V ready. Ctrl+C to quit.")
    session, msg = None, wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and (session is None or session.poll() is not None):
            session = subprocess.Popen([sys.executable, str(LISTEN)])


if __name__ == "__main__":
    main()
