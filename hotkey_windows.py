"""Windows global hotkeys: Win+F12 talks, Win+F11 types; pressed again, it cancels (put it in shell:startup).

It only sends `toggle` / `type` to the resident agent (agentd.py) and starts the agent if it is not running.

A two-key combo so it can be bound to a mouse button. Win+V is reserved by Windows (clipboard history).
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading

import agentd

MOD_WIN, MOD_NOREPEAT, WM_HOTKEY = 0x8, 0x4000, 0x312
KEYS = {1: (0x7B, "toggle", "Win+F12"), 2: (0x7A, "type", "Win+F11")}  # id: (virtual key, command, name)


def main() -> None:
    user32 = ctypes.windll.user32
    for hid, (vk, _cmd, name) in KEYS.items():
        if not user32.RegisterHotKey(None, hid, MOD_WIN | MOD_NOREPEAT, vk):
            sys.exit(f"{name} is already taken by another program.")
    print("Win+F12 talks, Win+F11 types. Ctrl+C to quit.")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: lets the agent's question take keyboard focus
            threading.Thread(target=agentd.send, args=(KEYS[msg.wParam][1],), daemon=True).start()


if __name__ == "__main__":
    main()
