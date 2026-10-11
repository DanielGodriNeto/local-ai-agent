"""Rounded dark text overlay, centered near the top of the screen. Thread-safe: call from any thread.

Keys: Esc closes (or answers "no" to a question), Enter answers "yes". Demo: python overlay.py "texto"
Windows: rounded via a transparent key colour. Hyprland/X11: Tk cannot round windows, the compositor does
(decoration rounding), so the card fills the whole window there.
"""
import ctypes
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

BG, FG, HINT = "#07090c", "#e8f1ee", "#5d6b68"
IDLE, LISTEN, GREEN, RED = "#5d6b68", "#82c341", "#82c341", "#ff6b6b"  # state colours (see make_logo.py)
KEY = "#010203"  # transparent key colour (Windows)
FONT_DIR = Path(__file__).parent / "fonts"
ASSETS = Path(__file__).parent / "assets"
FONTS = ("Josefin Sans", "JetBrains Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono", "Courier New")
RADIUS, PAD, MIN_W, MAX_FRAC = 34, 48, 460, 0.72


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


def text_size(s: str) -> int:
    """Big for a word, shrinking as the sentence grows."""
    return max(22, min(68, int(68 - 0.5 * max(0, len(s) - 8))))


class Overlay:
    def __init__(self, font: str = "", y_frac: float = 0.05):
        load_bundled_fonts()
        self.root = r = tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        self.round = sys.platform == "win32"
        if self.round:
            r.attributes("-transparentcolor", KEY)
        r.configure(bg=KEY if self.round else BG)
        have = set(tkfont.families(r))
        self.font = next((f for f in (font, *FONTS) if f and f in have), "TkFixedFont")
        self.y_frac = y_frac
        self.width = MIN_W
        self._logos: dict[str, tk.PhotoImage] = {}
        self.canvas = tk.Canvas(r, bg=KEY if self.round else BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._label, self._color, self._hint = "", IDLE, ""
        self.closed = threading.Event()
        self._jobs: queue.Queue = queue.Queue()
        self._answer = None  # (Event, [bool]) while a question is pending
        r.bind("<Return>", lambda _e: self._reply(True))
        r.bind("<Escape>", lambda _e: self._reply(False) if self._answer else self._close())
        r.bind("<Button-1>", lambda _e: self._answer or self._close())
        r.after(40, self._pump)
        self._draw()

    # ---- thread-safe API ----
    def status(self, _label: str, color: str = IDLE) -> None:
        def apply():
            self._color = color
            self._draw()
        self._jobs.put(apply)

    def text(self, s: str, color: str = FG) -> None:
        def apply():
            self._label, self._fg, self._hint = s, color, ""
            self._draw()
        self._jobs.put(apply)

    def ask(self, question: str, timeout: float = 60) -> bool:
        ev, box = threading.Event(), [False]

        def show():
            self._answer = (ev, box)
            self._color, self._label, self._fg = LISTEN, question, FG
            self._hint = "Enter  sim     Esc  não"
            self._draw()
            self.root.focus_force()
        self._jobs.put(show)
        ev.wait(timeout)
        self._answer = None
        return box[0]

    def hold(self, seconds: float) -> None:
        """Keep the overlay up (called from the worker thread); Esc/click ends it early."""
        self.closed.wait(seconds)
        self.close()

    def close(self) -> None:
        self._jobs.put(self._close)

    def run(self) -> None:
        self.root.mainloop()

    # ---- Tk thread ----
    def _reply(self, yes: bool) -> bool:
        if self._answer:
            ev, box = self._answer
            box[0] = yes
            ev.set()
        return False

    def _close(self) -> None:
        self.closed.set()
        self.root.destroy()

    def _pump(self) -> None:
        try:
            while True:
                self._jobs.get_nowait()()
        except queue.Empty:
            pass
        if not self.closed.is_set():
            self.root.after(40, self._pump)

    def _logo(self, color: str):
        if color not in self._logos:
            f = ASSETS / f"logo_{color.lstrip('#')}.png"
            self._logos[color] = tk.PhotoImage(file=str(f)) if f.exists() else None
        return self._logos[color]

    def _draw(self) -> None:
        """Width follows the text (up to MAX_FRAC of the screen); the font shrinks as the sentence grows."""
        r, c = self.root, self.canvas
        sw, asking = r.winfo_screenwidth(), bool(self._hint)
        size = 16 if asking else text_size(self._label)
        weight = "normal" if asking else "bold"
        wide = tkfont.Font(family=self.font, size=size, weight=weight).measure(self._label) + 2 * PAD
        max_w = int(sw * (0.5 if asking else MAX_FRAC))
        w = self.width = min(max_w, max(MIN_W, wide))
        c.delete("all")
        logo = self._logo(self._color)
        top = 22 + (logo.height() if logo else 8)
        t = c.create_text(w // 2, top + 18, text=self._label, fill=getattr(self, "_fg", FG), anchor="n",
                          width=w - 2 * PAD, justify="center", font=(self.font, size, weight))
        y1 = c.bbox(t)[3] if self._label else top + 18
        if asking:
            y1 = c.bbox(c.create_text(w // 2, y1 + 18, text=self._hint, fill=HINT, anchor="n", font=(self.font, 11)))[3]
        h = y1 + PAD
        self._rounded(2, 2, w - 2, h - 2, RADIUS if self.round else 0)
        c.tag_lower("card")
        if logo:
            c.create_image(w // 2, 22, image=logo, anchor="n")
        else:
            c.create_oval(w // 2 - 4, 22, w // 2 + 4, 30, fill=self._color, outline="")
        r.geometry(f"{w}x{h}+{(sw - w) // 2}+{int(r.winfo_screenheight() * self.y_frac)}")

    def _rounded(self, x1: int, y1: int, x2: int, y2: int, r: int) -> None:
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.canvas.create_polygon(pts, smooth=True, fill=BG, outline=self._color, width=2, tags="card")


if __name__ == "__main__":
    ui = Overlay()
    ui.status("", LISTEN)
    ui.text(" ".join(sys.argv[1:]) or "abre o navegador")
    threading.Thread(target=lambda: ui.hold(4), daemon=True).start()
    ui.run()
