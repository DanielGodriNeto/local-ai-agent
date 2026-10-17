"""Windows global hotkeys: Ctrl+Alt+Space talks, Ctrl+Alt+D types; pressed again, it cancels (put it in shell:startup).

It only sends `toggle` / `type` to the resident agent (agentd.py) and starts the agent if it is not running.

No Fn or F-keys: laptops remap those. Mouse software can send the combo from a button.
"""
import ctypes
import ctypes.wintypes as wt
import sys
import threading

import agentd

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x312
KEYS = {1: (0x20, "toggle", "Ctrl+Alt+Espaço"), 2: (0x44, "type", "Ctrl+Alt+D")}  # id: (virtual key, command, name)


def main() -> None:
    user32 = ctypes.windll.user32
    taken = [name for hid, (vk, _cmd, name) in KEYS.items() if not user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk)]
    if len(taken) == len(KEYS):
        sys.exit(f"{' e '.join(taken)} já são usados por outro programa.")
    for name in taken:  # one key lost to another program must not take the other one down with it
        print(f"{name} já é usado por outro programa; o ícone da bandeja faz o mesmo.")
    print("Ctrl+Alt+Space talks, Ctrl+Alt+D types. Ctrl+C to quit.")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: lets the agent's question take keyboard focus
            threading.Thread(target=agentd.send, args=(KEYS[msg.wParam][1],), daemon=True).start()


if __name__ == "__main__":
    main()
