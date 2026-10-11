"""Borderless text-only overlay, centered near the top of the screen. Thread-safe: call from any thread.

Keys: Esc closes (or answers "no" to a question), Enter answers "yes". Demo: python overlay.py
"""
import queue
import threading
import tkinter as tk

BG, BORDER, FG, MUTED = "#0f1115", "#2a2f38", "#f2f4f7", "#8b93a1"
AMBER, GREEN, RED = "#f5b942", "#7ddc9a", "#ff7a7a"
FONT = "Segoe UI"  # falls back to the system sans where missing


class Overlay:
    def __init__(self, y_frac: float = 0.14):
        self.root = r = tk.Tk()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.95)
        r.configure(bg=BORDER)
        self.y_frac = y_frac
        self.width = min(900, int(r.winfo_screenwidth() * 0.6))
        card = tk.Frame(r, bg=BG, padx=36, pady=24)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        self._status = tk.Label(card, bg=BG, fg=MUTED, font=(FONT, 10, "bold"), anchor="w")
        self._status.pack(fill="x")
        self._text = tk.Label(card, bg=BG, fg=FG, font=(FONT, 24), wraplength=self.width - 74, justify="center")
        self._text.pack(fill="x", pady=(14, 0))
        self.closed = threading.Event()
        self._jobs: queue.Queue = queue.Queue()
        self._answer = None  # (Event, [bool]) while a question is pending
        r.bind("<Return>", lambda _e: self._reply(True))
        r.bind("<Escape>", lambda _e: self._reply(False) if self._answer else self._close())
        r.bind("<Button-1>", lambda _e: self._answer or self._close())
        r.after(40, self._pump)
        self._place()

    # ---- thread-safe API ----
    def status(self, label: str, color: str = MUTED) -> None:
        self._jobs.put(lambda: self._status.config(text=f"●  {label}", fg=color))

    def text(self, s: str, color: str = FG) -> None:
        def apply():
            self._text.config(text=s, fg=color, font=(FONT, 24 if len(s) < 140 else 14))
            self._place()
        self._jobs.put(apply)

    def ask(self, question: str, timeout: float = 60) -> bool:
        ev, box = threading.Event(), [False]

        def show():
            self._answer = (ev, box)
            self._status.config(text="●  CONFIRMAR", fg=AMBER)
            self._text.config(text=f"{question}\n\nEnter = sim   ·   Esc = não", fg=FG, font=(FONT, 14))
            self._place()
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

    def _place(self) -> None:
        r = self.root
        r.update_idletasks()
        x = (r.winfo_screenwidth() - self.width) // 2
        r.geometry(f"{self.width}x{r.winfo_reqheight()}+{x}+{int(r.winfo_screenheight() * self.y_frac)}")


if __name__ == "__main__":
    ui = Overlay()
    ui.status("OUVINDO", AMBER)
    ui.text("abre o navegador e procura receitas de pão")
    threading.Thread(target=lambda: ui.hold(5), daemon=True).start()
    ui.run()
