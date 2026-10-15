"""Windows global hotkey: Win+F12 starts a voice session, pressed again it cancels (put it in shell:startup).

It only sends `toggle` to the resident agent (agentd.py) and starts the agent if it is not running.

A two-key combo so it can be bound to a mouse button. Win+V is reserved by Windows (clipboard history).
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading

import agentd

MOD_WIN, MOD_NOREPEAT, WM_HOTKEY, VK_F12 = 0x8, 0x4000, 0x312, 0x7B


def main() -> None:
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, MOD_WIN | MOD_NOREPEAT, VK_F12):
        sys.exit("Win+F12 is already taken by another program.")
    print("Win+F12 ready. Ctrl+C to quit.")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            threading.Thread(target=agentd.send, args=("toggle",), daemon=True).start()


if __name__ == "__main__":
    main()
