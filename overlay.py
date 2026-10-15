"""Rounded dark text overlay. Thread-safe: call from any thread (Tk work is queued onto the Tk thread).

kind="main"   centered near the top: live speech, answers, questions.
kind="corner" compact row in a screen corner: Claude Code progress.
Keys: Esc closes (or answers "no"), Enter answers "yes", 1-9 picks an option. In edit mode the window drags.
Windows: rounded via a transparent key colour. Hyprland/X11: Tk cannot round windows, the compositor does.
Demo: python overlay.py "texto" [corner]
"""
import ctypes
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Callable, Optional

import config
import make_logo

FONT_DIR = Path(__file__).parent / "fonts"
FONTS = ("Josefin Sans", "JetBrains Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono", "Courier New")
LISTEN, GREEN, IDLE, RED = "accent", "accent", "idle", "error"  # semantic state keys, resolved via ui["colors"]
KEY = "#010203"  # transparent key colour (Windows)
ANCHORS = {"n": (0.5, 0), "s": (0.5, 1), "c": (0.5, 0.5), "ne": (1, 0), "nw": (0, 0), "se": (1, 1), "sw": (0, 1)}


def load_bundled_fonts() -> None:
    """Make fonts/*.ttf usable by this process only (no system install). Failure just means a fallback font."""
    for ttf in FONT_DIR.glob("*.ttf"):
        try:
            if sys.platform == "win32":
                ctypes.windll.gdi32.AddFontResourceExW(str(ttf), 0x10, 0)  # FR_PRIVATE
            else:
                ctypes.CDLL("libfontconfig.so.1").FcConfigAppFontAddFile(None, str(ttf).encode())
        except (OSError, AttributeError):
            pass


def mix(a: str, b: str, t: float) -> str:
    """Blend hex colour a toward b by t (0..1)."""
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def text_size(s: str, lo: int, hi: int) -> int:
    """Big for a word, shrinking as the sentence grows (reaches `lo` around 90 characters)."""
    return int(max(lo, min(hi, hi - (hi - lo) * max(0, len(s) - 8) / 80)))


class Overlay:
    def __init__(self, ui: Optional[dict] = None, kind: str = "main", master: Optional[tk.Misc] = None):
        load_bundled_fonts()
        self.ui, self.kind = ui or config.load()["ui"], kind
        self.root = r = tk.Toplevel(master) if master else tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        self.round = sys.platform == "win32"
        if self.round:
            r.attributes("-transparentcolor", KEY)
        bg = KEY if self.round else self.ui["colors"]["bg"]
        r.configure(bg=bg)
        self.canvas = tk.Canvas(r, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._label, self._fg, self._hint, self._state, self._tag = "", "fg", "", IDLE, ""
        self._images: dict = {}
        self.closed = threading.Event()
        self.edit = False
        self.on_move: Optional[Callable[[str, float, float], None]] = None
        self._jobs: queue.Queue = queue.Queue()
        self._answer = None  # (Event, [value]) while a question/choice is pending
        self._choices = 0
        self._drag = (0, 0)
        r.bind("<Return>", lambda _e: self._reply(True))
        r.bind("<Escape>", lambda _e: self._reply(None) if self._answer else self._close())
        r.bind("<Key>", self._key)
        r.bind("<ButtonPress-1>", self._press)
        r.bind("<B1-Motion>", self._motion)
        r.bind("<ButtonRelease-1>", self._release)
        r.after(40, self._pump)
        self._draw()

    # ---- thread-safe API ----
    def status(self, _label: str, state: str = IDLE) -> None:
        def apply():
            self._state, self._tag = state, _label
            self._draw()
        self._jobs.put(apply)

    def text(self, s: str, color: str = "fg") -> None:
        def apply():
            self._label, self._fg, self._hint = s, color, ""
            self._draw()
        self._jobs.put(apply)

    def ask(self, question: str, timeout: float = 60) -> bool:
        return bool(self._wait(question, "Enter  sim     Esc  não", 0, timeout))

    def choose(self, question: str, options: list[str], timeout: float = 120) -> Optional[int]:
        """Numbered list; returns the picked index or None (Esc / timeout). resolve_choice() lets voice answer too."""
        listing = "\n".join(f"{i}   {o}" for i, o in enumerate(options, 1))
        got = self._wait(f"{question}\n\n{listing}", "1-9  escolhe     Esc  cancela", len(options), timeout)
        return got if isinstance(got, int) and not isinstance(got, bool) else None

    def resolve_choice(self, index: int) -> None:
        if self._answer:
            self._answer[1][0] = index
            self._answer[0].set()

    def hold(self, seconds: float) -> None:
        """Keep the overlay up (called from the worker thread); Esc/click ends it early."""
        self.closed.wait(seconds)
        self.close()

    def close(self) -> None:
        self._jobs.put(self._close)

    def run(self) -> None:
        self.root.mainloop()

    def apply(self, ui: dict) -> None:
        """Tk thread only (settings window): adopt new settings and redraw."""
        self.ui = ui
        bg = KEY if self.round else ui["colors"]["bg"]
        self.root.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self._draw()

    # ---- internals (Tk thread) ----
    def _wait(self, question: str, hint: str, choices: int, timeout: float):
        ev, box = threading.Event(), [None]
        self._answer, self._choices = (ev, box), choices  # set now, so a close before the redraw still answers no
        if self.closed.is_set():  # already closed (cancelled): nobody can answer
            self._answer, self._choices = None, 0
            return None

        def show():
            self._state, self._label, self._fg, self._hint = LISTEN, question, "fg", hint
            self._draw()
            self.root.focus_force()
        self._jobs.put(show)
        ev.wait(timeout)
        self._answer, self._choices = None, 0
        return box[0]

    def _reply(self, value) -> None:
        if self._answer and (value is None or self._choices == 0):
            self._answer[1][0] = value
            self._answer[0].set()

    def _key(self, e) -> None:
        if self._choices and e.char.isdigit() and 1 <= int(e.char) <= self._choices:
            self.resolve_choice(int(e.char) - 1)

    def _press(self, e) -> None:
        if self.edit:
            self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        elif not self._answer:
            self._close()

    def _motion(self, e) -> None:
        if self.edit:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _release(self, _e) -> None:
        if not self.edit:
            return
        r, k = self.root, self.ui[self.kind]
        fx, fy = ANCHORS[k["anchor"]]
        k["x_pct"] = round((r.winfo_x() + fx * r.winfo_width()) / r.winfo_screenwidth() * 100, 1)
        k["y_pct"] = round((r.winfo_y() + fy * r.winfo_height()) / r.winfo_screenheight() * 100, 1)
        if self.on_move:
            self.on_move(self.kind, k["x_pct"], k["y_pct"])

    def _close(self) -> None:
        self.closed.set()  # first, so a question asked from now on sees it
        self._reply(None)  # a pending question gets "no", never a wait until timeout
        self.root.destroy()

    def _pump(self) -> None:
        try:
            while True:
                self._jobs.get_nowait()()
        except queue.Empty:
            pass
        if not self.closed.is_set():
            self.root.after(40, self._pump)

    def _color(self, key: str) -> str:
        return self.ui["colors"].get(key, key)

    def _logo(self, state: str, px: int):
        hexcolor = self._color(state)
        if (hexcolor, px) not in self._images:
            png = make_logo.logo_png(hexcolor, px)
            self._images[(hexcolor, px)] = tk.PhotoImage(file=str(png)) if png else None
        return self._images[(hexcolor, px)]

    def _draw(self) -> None:
        """Width follows the text (up to max_width_pct of the screen); the font shrinks as the sentence grows."""
        r, c, k = self.root, self.canvas, self.ui[self.kind]
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        asking, pad = bool(self._hint), k["pad"]
        size = 16 if asking else text_size(self._label, k["size_min"], k["size_max"])
        spec = (self._font(), size, "normal" if asking else "bold")
        wide = tkfont.Font(family=spec[0], size=size, weight=spec[2]).measure(self._label.split("\n")[0])
        max_w = int(sw * (0.5 if asking else k["max_width_pct"] / 100))
        logo = self._logo(self._state, k["logo_px"])
        c.delete("all")
        fg = self._color(self._fg)
        if self.kind == "corner":
            w, h = self._draw_row(c, spec, wide, max_w, pad, logo, fg, k)
        else:
            w, h = self._draw_column(c, spec, wide, max_w, pad, logo, fg, k, asking)
        self._rounded(w, h, k["radius"] if self.round else 0)
        fx, fy = ANCHORS[k["anchor"]]
        x = min(max(0, k["x_pct"] / 100 * sw - fx * w), max(0, sw - w))
        y = min(max(0, k["y_pct"] / 100 * sh - fy * h), max(0, sh - h))
        r.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def _draw_column(self, c, spec, wide, max_w, pad, logo, fg, k, asking):
        w = min(max_w, max(k["min_width"], wide + 2 * pad))
        top = 22 + (logo.height() if logo else 8)
        t = c.create_text(w // 2, top + 18, text=self._label, fill=fg, anchor="n", width=w - 2 * pad,
                          justify="center", font=spec)
        y1 = c.bbox(t)[3] if self._label else top + 18
        if asking:
            hint = c.create_text(w // 2, y1 + 18, text=self._hint, fill=self._color("hint"), anchor="n",
                                 font=(spec[0], 11))
            y1 = c.bbox(hint)[3]
        if logo:
            c.create_image(w // 2, 22, image=logo, anchor="n")
        else:
            c.create_oval(w // 2 - 4, 22, w // 2 + 4, 30, fill=self._color(self._state), outline="")
        return w, y1 + pad

    def _draw_row(self, c, spec, wide, max_w, pad, logo, fg, k):
        lw, gap = (logo.width() if logo else 10), 14
        tw = min(wide, max_w - 2 * pad - lw - gap)
        w = max(k["min_width"], tw + 2 * pad + lw + gap)
        t = c.create_text(pad + lw + gap, pad, text=self._label, fill=fg, anchor="nw", width=max(tw, 40) + 4, font=spec)
        h = max(c.bbox(t)[3] if self._label else 0, pad + (logo.height() if logo else 10)) + pad
        if logo:
            c.create_image(pad, h // 2, image=logo, anchor="w")
        else:
            c.create_oval(pad, h // 2 - 5, pad + 10, h // 2 + 5, fill=self._color(self._state), outline="")
        return w, h

    def _font(self) -> str:
        have = set(tkfont.families(self.root))
        return next((f for f in (self.ui.get("font", ""), *FONTS) if f and f in have), "TkFixedFont")

    def _rounded(self, w: int, h: int, r: int) -> None:
        """Cyberpunk card: dim glow, neon edge, accent tabs and a small HUD tag."""
        c, bg, neon = self.canvas, self._color("bg"), self._color(self._state)
        x1, y1, x2, y2 = 3, 3, w - 3, h - 3
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        c.create_polygon(pts, smooth=True, fill=bg, outline=mix(bg, neon, 0.3), width=6, tags="card")
        c.create_polygon(pts, smooth=True, fill="", outline=neon, width=2, tags="card")
        c.create_line(x1 + r, y1, x1 + r + 46, y1, fill=neon, width=5, tags="card")
        c.create_line(x2 - r - 46, y2, x2 - r, y2, fill=neon, width=5, tags="card")
        if self._tag:
            c.create_text(x1 + r + 8, y1 + 12, text=self._tag.upper(), fill=neon, anchor="w",
                          font=("Consolas", 9, "bold"), tags="card")
        c.tag_lower("card")


if __name__ == "__main__":
    ui = Overlay(kind="corner" if "corner" in sys.argv else "main")
    ui.status("", LISTEN)
    ui.text(" ".join(a for a in sys.argv[1:] if a != "corner") or "abre o navegador")
    threading.Thread(target=lambda: ui.hold(4), daemon=True).start()
    ui.run()
