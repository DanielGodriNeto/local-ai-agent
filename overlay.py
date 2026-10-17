"""Light, Apple-style overlay (Qt). Thread-safe: call from any thread (the work is queued onto the GUI thread).

kind="main"   a pill near the top that grows into a card: live speech, answers, questions with buttons.
kind="corner" a Live Activity pill in a screen corner: Claude Code progress, background tasks.
Keys: Esc closes (or answers "no"), Enter answers "yes" (or presses the focused button), 1-9 picks an option,
Tab moves between buttons. Follows the system theme, high contrast and "show animations" settings.
Demo: python overlay.py "texto" [corner|ask|choose]
"""
import ctypes
import sys
import threading
import time
from typing import Optional

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRectF, Qt, QVariantAnimation, Signal, Slot
from PySide6.QtGui import (QColor, QCursor, QFont, QFontDatabase, QFontMetrics, QGuiApplication, QPainter, QPalette,
                           QPen)
from PySide6.QtWidgets import (QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea,
                               QVBoxLayout, QWidget)

import config

FONTS = ("Segoe UI Variable Text", "SF Pro Text", "Segoe UI", "Cantarell", "Noto Sans")
LISTEN, GREEN, IDLE, RED = "accent", "done", "idle", "error"  # state keys, resolved by the palette
ARM_S = 0.5  # a question ignores keys this long after it appears
LONG = 280  # characters; longer text is laid out for reading (smaller, left-aligned, selectable)
ANCHORS = {"n": (0.5, 0), "s": (0.5, 1), "c": (0.5, 0.5), "ne": (1, 0), "nw": (0, 0), "se": (1, 1), "sw": (0, 1)}
MARGIN = 28  # transparent room around the card for its shadow
DOT, GAP = 10, 12
ACCENTS = {  # name -> (light, dark), Apple system colours
    "blue": ("#007aff", "#0a84ff"), "teal": ("#30b0c7", "#40c8e0"), "green": ("#34c759", "#30d158"),
    "orange": ("#ff9500", "#ff9f0a"), "pink": ("#ff2d55", "#ff375f"), "graphite": ("#8e8e93", "#98989d"),
}
PALETTES = {
    "light": {"surface": (251, 251, 253, 242), "line": (0, 0, 0, 22), "fg": "#1d1d1f", "muted": "#6e6e73",
              "button": (0, 0, 0, 14), "done": "#34c759", "error": "#ff3b30"},
    "dark": {"surface": (30, 30, 32, 240), "line": (255, 255, 255, 40), "fg": "#f5f5f7", "muted": "#98989d",
             "button": (255, 255, 255, 26), "done": "#30d158", "error": "#ff453a"},
}


def app() -> QApplication:
    """The one QApplication of this process (created on first use)."""
    return QApplication.instance() or QApplication(sys.argv[:1])


def _anchor() -> QWidget:
    """Never shown. Overlay windows are its children, so Qt owns them: Python's garbage collector, which may run on
    any thread, never destroys a widget (that must happen on the GUI thread, via deleteLater)."""
    global _ANCHOR
    if _ANCHOR is None:
        _ANCHOR = QWidget()
    return _ANCHOR


_ANCHOR: Optional[QWidget] = None


def text_size(s: str, lo: int, hi: int) -> int:
    """Big for a word, shrinking as the sentence grows (reaches `lo` around 90 characters)."""
    return int(max(lo, min(hi, hi - (hi - lo) * max(0, len(s) - 8) / 80)))


def _spi(action: int, struct=None) -> int:
    if sys.platform != "win32":
        return -1
    out = struct if struct is not None else ctypes.c_int(0)
    ctypes.windll.user32.SystemParametersInfoW(action, ctypes.sizeof(out), ctypes.byref(out), 0)
    return out.value if struct is None else 0


def reduced_motion() -> bool:
    return _spi(0x1042) == 0  # SPI_GETCLIENTAREAANIMATION off = "show animations" disabled


def high_contrast() -> bool:
    class HC(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint), ("flags", ctypes.c_uint), ("scheme", ctypes.c_wchar_p)]
    if sys.platform != "win32":
        return False
    hc = HC(ctypes.sizeof(HC), 0, None)
    _spi(0x42, hc)  # SPI_GETHIGHCONTRAST
    return bool(hc.flags & 1)


def system_accent() -> Optional[str]:
    """The accent colour the user picked in Windows (Settings > Personalisation > Colours), else the desktop's via Qt
    (KDE, GNOME, Hyprland with a Qt theme). None when there is none to follow."""
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
                abgr = winreg.QueryValueEx(k, "AccentColor")[0]
            return QColor(abgr & 0xFF, (abgr >> 8) & 0xFF, (abgr >> 16) & 0xFF).name()
        except OSError:
            pass
    c = app().palette().color(QPalette.ColorRole.Accent)
    return c.name() if c.isValid() else None


def accent(name: str, dark: bool) -> str:
    """A named Apple colour, or the system's ("system", the default): lifted a little on a dark surface, as Apple's
    dark-mode colours are, so it never sinks into the background."""
    if name in ACCENTS:
        return ACCENTS[name][dark]
    c = QColor(system_accent() or ACCENTS["blue"][dark])
    if dark and c.lightness() < 140:
        c = c.lighter(135)
    if not dark and c.lightness() > 170:
        c = c.darker(130)
    return c.name()


def palette(ui: dict) -> dict:
    theme = ui.get("theme", "auto")
    if theme == "auto":
        theme = "dark" if app().styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
    p = dict(PALETTES[theme])
    p["accent"] = accent(ui.get("accent", "system"), theme == "dark")
    p["idle"], p["hc"] = p["muted"], high_contrast()
    if p["hc"]:  # solid surface, strong edge
        p["surface"], p["line"] = p["surface"][:3] + (255,), QColor(p["fg"]).getRgb()
    return p


def family(ui: dict) -> str:
    have = set(QFontDatabase.families())
    return next((f for f in (ui.get("font", ""), *FONTS) if f and f in have), app().font().family())


class Bridge(QObject):
    """bridge.call.emit(fn) runs fn on the GUI thread, from any thread."""
    call = Signal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.call.connect(self._run)

    @Slot(object)
    def _run(self, fn) -> None:
        fn()


class _Dot(QWidget):
    """State light; breathes while listening or thinking."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(DOT, DOT)
        self.color, self.alpha = QColor("#8e8e93"), 1.0
        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(1.0)
        self.pulse.setKeyValueAt(0.5, 0.3)
        self.pulse.setEndValue(1.0)
        self.pulse.setDuration(1200)
        self.pulse.setLoopCount(-1)
        self.pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.pulse.valueChanged.connect(self._fade)

    def set(self, color: str, pulse: bool) -> None:
        self.color = QColor(color)
        if pulse and self.pulse.state() != QVariantAnimation.State.Running:
            self.pulse.start()
        elif not pulse:
            self.pulse.stop()
            self.alpha = 1.0
        self.update()

    def _fade(self, v: float) -> None:
        self.alpha = v
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        c.setAlphaF(self.alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(self.rect())


class _Card(QWidget):
    def __init__(self):
        super().__init__()
        self.radius, self.fill, self.line, self.line_w = 24.0, QColor("white"), QColor(0, 0, 0, 22), 1.0

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.line, self.line_w))
        p.setBrush(self.fill)
        half = self.line_w / 2
        p.drawRoundedRect(QRectF(self.rect()).adjusted(half, half, -half, -half), self.radius, self.radius)


class _Window(QWidget):
    def __init__(self, owner: "Overlay"):
        super().__init__(_anchor(), Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # never steals focus, except for a question

    def keyPressEvent(self, e) -> None:
        self.owner._key(e)

    def mousePressEvent(self, _e) -> None:
        self.owner._click()


class Overlay:
    def __init__(self, ui: Optional[dict] = None, kind: str = "main"):
        app()
        self.ui, self.kind = ui or config.load()["ui"], kind
        self.closed = threading.Event()
        self._label, self._fg, self._state, self._tag = "", "fg", IDLE, ""
        self._ask = None  # [] = yes/no question, [options] = choice, "input" = typed text; None = not asking
        self._built = None  # the _ask the current buttons were made for
        self._expandable = False  # a corner pill that opens into the full card when clicked
        self._answer = None  # (Event, [value]) while a question/choice is pending
        self._choices, self._shown, self._anims, self._default = 0, False, [], None
        self._armed = 0.0  # keys answer a question only after this time.monotonic()
        self._build()
        self._bridge = Bridge(self.win)
        self._draw()

    # ---- thread-safe API ----
    def _do(self, fn) -> None:
        try:
            self._bridge.call.emit(fn)
        except RuntimeError:  # the window is already gone (closed and deleted): nothing left to update
            pass

    gui = _do  # gui(fn) runs fn on the Qt thread, from any thread

    def status(self, label: str, state: str = IDLE) -> None:
        def apply():
            self._state, self._tag = state, label
            self._draw()
        self._do(apply)

    def text(self, s: str, color: str = "fg") -> None:
        def apply():
            self._label, self._fg = s, color
            self._draw()
        self._do(apply)

    def ask(self, question: str, timeout: float = 60) -> bool:
        return bool(self._wait(question, [], timeout))

    def choose(self, question: str, options: list[str], timeout: float = 120) -> Optional[int]:
        """One button per option (the first 9); returns the picked index or None (Esc / timeout). resolve_choice() lets voice
        answer too."""
        got = self._wait(question, options[:9], timeout)  # keys 1-9, and the card stays on screen
        return got if isinstance(got, int) and not isinstance(got, bool) else None

    def prompt(self, question: str, timeout: float = 300) -> Optional[str]:
        """A text box: Enter sends, Esc cancels. Returns the text, or None."""
        got = self._wait(question, "input", timeout)
        return got.strip() if isinstance(got, str) and got.strip() else None

    def to_corner(self) -> None:
        """Shrink into the corner pill (a long task moving to the background); a click opens it again."""
        def apply():
            self.kind, self._expandable = "corner", True
            self._draw()
        self._do(apply)

    def resolve_choice(self, index: int) -> None:
        if self._answer:
            self._answer[1][0] = index
            self._answer[0].set()

    def hold(self, seconds: Optional[float]) -> None:
        """Keep the overlay up (called from the worker thread); Esc/click ends it early. None = until then."""
        self.closed.wait(seconds)
        self.close()

    def close(self) -> None:
        self._do(self._close)

    def run(self) -> None:
        app().exec()

    def apply(self, ui: dict) -> None:
        """Adopt new settings and redraw (settings window preview)."""
        def apply():
            self.ui = ui
            self._draw()
        self._do(apply)

    # ---- internals (GUI thread) ----
    def _build(self) -> None:
        w = self.win = _Window(self)
        w.setAccessibleName("Assistente")
        self.card = card = _Card()
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(MARGIN)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 60))
        card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setSpacing(GAP)
        self.dot = _Dot()
        self.dot_box = QVBoxLayout()
        self.dot_box.addWidget(self.dot)
        self.dot_box.addStretch()
        row.addLayout(self.dot_box)
        col = QVBoxLayout()
        col.setSpacing(6)
        row.addLayout(col)
        self.cap = QLabel()
        self.body = QLabel()
        for label in (self.cap, self.body):  # AI and hook text is never markup: no fake buttons, links or images
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.body.setWordWrap(True)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.buttons = QWidget()
        self.button_box = QVBoxLayout(self.buttons)
        self.button_box.setContentsMargins(0, 6, 0, 0)
        for wid in (self.cap, self.scroll, self.buttons):
            col.addWidget(wid)

    def _wait(self, question: str, options, timeout: float):
        ev, box = threading.Event(), [None]
        # set now, so a close before the redraw answers no
        self._answer, self._choices = (ev, box), len(options) if isinstance(options, list) else 0
        if self.closed.is_set():  # already closed (cancelled): nobody can answer
            self._answer, self._choices = None, 0
            return None

        def show():
            if self.closed.is_set():
                return
            self._state, self._label, self._fg, self._ask = LISTEN, question, "fg", options
            self._armed = time.monotonic() + ARM_S
            if self.kind == "corner":  # a background task asking: open the card, but never take the keyboard
                self.kind = "main"
                self._draw()
                return
            self._draw()
            self.win.activateWindow()
            self.win.raise_()
            self._default.setFocus()
        self._do(show)
        ev.wait(timeout)
        self._answer, self._choices = None, 0

        def done():
            self._ask = None
            self._draw()
        self._do(done)
        return box[0]

    def _reply(self, value) -> None:
        if self._answer and not self._answer[0].is_set() and (value is None or self._choices == 0):
            self._answer[1][0] = value
            self._answer[0].set()

    def _key(self, e) -> None:
        key, digit = e.key(), e.text()
        if key == Qt.Key.Key_Escape:
            self._reply(None) if self._answer else self._close()
        elif self._answer and time.monotonic() < self._armed:
            return  # typing meant for another window when the question popped up: it must not answer it
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.win.focusWidget()
            if isinstance(focus, QPushButton):  # the text box sends on its own; nothing focused answers nothing
                focus.click()
        elif self._choices and digit.isdigit() and 1 <= int(digit) <= self._choices:
            self.resolve_choice(int(digit) - 1)

    def _click(self) -> None:
        if self._expandable and self.kind == "corner":
            self.kind = "main"
            self._draw()
        elif not self._answer:
            self._close()

    def _close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()  # first, so a question asked from now on sees it
        self._reply(None)  # a pending question gets "no", never a wait until timeout
        fade = self._animate(b"windowOpacity", self.win.windowOpacity(), 0.0, 140)
        fade.finished.connect(self.win.close)
        fade.finished.connect(self.win.deleteLater)  # destroyed here, on the GUI thread

    def _animate(self, prop: bytes, start, end, ms: int) -> QPropertyAnimation:
        a = QPropertyAnimation(self.win, prop, self.win)
        a.setStartValue(start)
        a.setEndValue(end)
        a.setDuration(ms)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anims = [x for x in self._anims if x.state() == QPropertyAnimation.State.Running] + [a]
        a.start()
        return a

    def _buttons(self, p: dict, font: str) -> None:
        if self._built is self._ask:  # streamed text redraws often: keep the buttons (and what was typed)
            return
        self._built = self._ask
        while self.button_box.count():
            self.button_box.takeAt(0).widget().deleteLater()
        if self._ask is None:
            self.buttons.hide()
            return
        base = (f"QPushButton {{ font-family: '{font}'; font-size: {round(14 * self.ui.get('text_scale', 1.0))}px; "
                "border-radius: 10px; padding: 8px 18px; border: 2px solid transparent; }} "
                f"QPushButton:focus {{ border-color: {QColor(p['accent']).lighter(140).name()}; }}")
        quiet = "background: rgba(%d,%d,%d,%d); color: %s;" % (*p["button"], p["fg"])
        loud = f"background: {p['accent']}; color: white; font-weight: 600;"
        if self._ask == "input":
            box = self._default = QLineEdit()
            box.setPlaceholderText("Escreva e tecle Enter")
            box.setAccessibleName("Pedido")
            box.setMinimumWidth(round(420 * self.ui.get("text_scale", 1.0)))
            box.setStyleSheet(f"QLineEdit {{ font-family: '{font}'; font-size: {round(16 * self.ui.get('text_scale', 1.0))}px; "
                              f"color: {p['fg']}; background: rgba(%d,%d,%d,%d); border: 2px solid transparent; "
                              f"border-radius: 10px; padding: 8px 12px; }} QLineEdit:focus {{ border-color: {p['accent']}; }}"
                              % p["button"])
            box.returnPressed.connect(lambda: self._reply(box.text()))
            self.button_box.addWidget(box)
        elif not self._ask:
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addStretch()
            for label, act, look in (("Não", lambda: self._reply(None), quiet), ("Sim", lambda: self._reply(True), loud)):
                b = QPushButton(label)
                if label == "Não":  # the focus: a stray Enter answers no
                    self._default = b
                b.setStyleSheet(base + f"QPushButton {{ {look} }}")
                b.clicked.connect(act)
                row.addWidget(b)
            self.button_box.addWidget(holder)
        else:
            for i, option in enumerate(self._ask):
                b = QPushButton(f"{i + 1}   {option}")
                if i == 0:
                    self._default = b
                b.setStyleSheet(base + f"QPushButton {{ {quiet} text-align: left; }}")
                b.clicked.connect(lambda _=False, i=i: self.resolve_choice(i))
                self.button_box.addWidget(b)
            cancel = QPushButton("Cancelar")
            cancel.setStyleSheet(base + f"QPushButton {{ background: transparent; color: {p['muted']}; }}")
            cancel.clicked.connect(lambda: self._reply(None))
            self.button_box.addWidget(cancel)
        for i in range(self.button_box.count()):  # now, not on the next event loop turn: the size depends on them
            self.button_box.itemAt(i).widget().show()
        self.buttons.show()

    def _draw(self) -> None:
        """Width follows the text (up to max_width_pct of the screen); the font shrinks as the sentence grows."""
        if self.closed.is_set():
            return
        p, k, scale = palette(self.ui), self.ui[self.kind], self.ui.get("text_scale", 1.0)
        fam, asking = family(self.ui), self._ask is not None
        reading = asking or (len(self._label) > LONG and self.kind == "main")
        self.card.fill, self.card.line = QColor(*p["surface"]), QColor(*p["line"])
        self.card.line_w = 2.0 if p["hc"] else 1.0
        self.dot.set(p.get(self._state, self._state), self._state in (LISTEN, IDLE) and not reduced_motion())
        cap_font = QFont(fam, round(12 * scale), QFont.Weight.DemiBold)
        self.cap.setFont(cap_font)
        self.cap.setStyleSheet(f"color: {p['muted']};")
        self.cap.setText(self._tag)
        self.cap.setVisible(bool(self._tag))
        size = (15 if reading else text_size(self._label, k["size_min"], k["size_max"])) * scale
        font = QFont(fam, round(size), QFont.Weight.Normal if reading else QFont.Weight.Medium)
        self.body.setFont(font)
        self.body.setStyleSheet(f"color: {p.get(self._fg, p['fg'])};")
        shown = self._label
        if self.kind == "corner" and (len(shown) > 60 or "\n" in shown):
            shown = shown.split("\n")[0][:60].rstrip(" .") + "…"  # a pill holds one line; a click opens the rest
        self.body.setText(shown)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse if reading and not asking
                                          else Qt.TextInteractionFlag.NoTextInteraction)
        self.scroll.setVisible(bool(self._label))
        self._buttons(p, fam)
        self._place(p, k, font, cap_font, reading)

    def _place(self, p: dict, k: dict, font: QFont, cap_font: QFont, reading: bool) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area, pad, fm = screen.availableGeometry(), k["pad"], QFontMetrics(font)
        self.card.layout().setContentsMargins(pad, pad - 4, pad + 4, pad - 4)
        side = DOT + GAP + 2 * pad + 4
        max_w = int(area.width() * k["max_width_pct"] / 100)
        if reading:
            max_w = min(max_w, fm.horizontalAdvance("0" * 70) + side)
        natural = max([fm.horizontalAdvance(line) + 6 for line in self.body.text().split("\n")]
                      + [QFontMetrics(cap_font).horizontalAdvance(self._tag) + 2, self.buttons.sizeHint().width()
                         if self._ask is not None else 0])
        w = min(max_w, max(k["min_width"] if self._label else 0, natural + side))
        inner = w - side
        body_h = self.body.heightForWidth(inner) if self._label else 0
        limit = (int(area.height() * 0.8) - 2 * pad - (QFontMetrics(cap_font).height() + 6 if self._tag else 0)
                 - (self.buttons.sizeHint().height() + 6 if self._ask is not None else 0))  # the whole card fits
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if body_h > limit
                                               else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFixedSize(inner, max(1, min(body_h, limit)))
        first = QFontMetrics(cap_font if self._tag else font).height()
        self.dot_box.setContentsMargins(0, max(0, (first - DOT) // 2), 0, 0)
        self.card.setFixedWidth(w)
        h = self.card.layout().sizeHint().height()
        self.card.setFixedHeight(h)
        self.card.radius = min(h / 2, k["radius"])
        self.card.update()
        fx, fy = ANCHORS[k["anchor"]]
        x = min(max(area.left(), area.left() + k["x_pct"] / 100 * area.width() - fx * w), area.right() - w)
        y = min(max(area.top(), area.top() + k["y_pct"] / 100 * area.height() - fy * h), area.bottom() - h)
        target = QPoint(int(x) - MARGIN, int(y) - MARGIN)
        self.win.setFixedSize(w + 2 * MARGIN, h + 2 * MARGIN)
        if self._shown:
            self.win.move(target)
            return
        self._shown = True
        self.win.setWindowOpacity(0.0)
        self.win.move(target)
        self.win.show()
        self._animate(b"windowOpacity", 0.0, 1.0, 180)
        if not reduced_motion():  # arrives from the edge it is anchored to
            self._animate(b"pos", target + QPoint(0, -10 if fy < 0.5 else 10), target, 260)


if __name__ == "__main__":
    args = sys.argv[1:]
    ui = Overlay(kind="corner" if "corner" in args else "main")
    words = " ".join(a for a in args if a not in ("corner", "ask", "choose")) or "abre o navegador"

    def demo():
        if "ask" in args:
            ui.status("Confirmar")
            print("resposta:", ui.ask(words))
        elif "choose" in args:
            ui.status("Escolha")
            print("escolha:", ui.choose(words, ["Chrome", "Firefox", "Edge"]))
        else:
            ui.status("Ouvindo", LISTEN)
            ui.text(words)
        ui.hold(4)
    threading.Thread(target=demo, daemon=True).start()
    ui.run()
