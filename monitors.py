"""Monitor numbering: 1 = primary, then the rest left to right (top to bottom). `python monitors.py` lists them."""
import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys


def _windows() -> list[dict]:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, not scaled ones
    except (OSError, AttributeError):
        pass
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    def each(hmon, _dc, _rect, _p):
        info = Info(cbSize=ctypes.sizeof(Info))
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        found.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top, "primary": bool(info.dwFlags & 1)})
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(each), 0)
    return found


def _hyprland() -> list[dict]:
    out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=5).stdout
    return [{"x": m["x"], "y": m["y"], "w": m["width"], "h": m["height"], "primary": m["x"] == 0 and m["y"] == 0}
            for m in json.loads(out)]


def list_monitors() -> list[dict]:
    """[{n, x, y, w, h, primary}], n starting at 1. Falls back to one monitor if the OS cannot be queried."""
    try:
        found = _windows() if sys.platform == "win32" else _hyprland()
    except (OSError, ValueError, subprocess.SubprocessError):
        found = []
    found = found or [{"x": 0, "y": 0, "w": 0, "h": 0, "primary": True}]
    found.sort(key=lambda m: (not m["primary"], m["x"], m["y"]))
    return [{"n": i, **m} for i, m in enumerate(found, 1)]


if __name__ == "__main__":
    for m in list_monitors():
        print(f"Monitor {m['n']}{' (principal)' if m['n'] == 1 else ''}: {m['w']}x{m['h']} em {m['x']},{m['y']}")
