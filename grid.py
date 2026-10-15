"""The number grid and the click marker: see-through windows placed from physical pixels, so they line up with UI
Automation and the mouse on any DPI and monitor. Demo: python grid.py [marker]"""
import sys
from typing import Optional

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

import config
import overlay


class Grid(QWidget):
    def __init__(self, rect: tuple[int, int, int, int], marker: bool = False, owner: Optional[QWidget] = None):
        super().__init__(owner or overlay._anchor(), Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("agent-grid")  # Hyprland moves it by this title
        self.marker, self.accent = marker, QColor(overlay.palette(config.load()["ui"])["accent"])
        self.show()
        self.place(rect)

    def place(self, rect: tuple[int, int, int, int]) -> None:
        self.setGeometry(logical(rect))
        self.update()


    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.marker:
            ring = r.adjusted(4, 4, -4, -4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 230), 6))
            p.drawEllipse(ring)
            p.setPen(QPen(self.accent, 3))
            p.drawEllipse(ring)  # a ring, not a dot: the label under it stays readable
            return
        p.fillRect(r, QColor(0, 0, 0, 36))
        for i in range(4):  # a dark edge under a light line: visible on any background
            for color, width in ((QColor(0, 0, 0, 110), 3), (QColor(255, 255, 255, 220), 1.2)):
                p.setPen(QPen(color, width))
                x, y = r.width() * i / 3, r.height() * i / 3
                p.drawLine(int(x), 0, int(x), int(r.height()))
                p.drawLine(0, int(y), int(r.width()), int(y))
        cw, ch = r.width() / 3, r.height() / 3
        size = max(18.0, min(56.0, min(cw, ch) * 0.3))
        font = QFont(overlay.family(config.load()["ui"]))
        font.setPixelSize(round(size * 0.6))
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        for n in range(9):
            cell = QRectF((n % 3) * cw, (n // 3) * ch, cw, ch)
            pill = QRectF(0, 0, size, size)
            pill.moveCenter(cell.center())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.accent)
            p.drawRoundedRect(pill, size / 2, size / 2)
            p.setPen(QColor("white"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, str(n + 1))



def logical(rect: tuple[int, int, int, int]) -> QRect:
    """Physical pixels -> Qt's. Each screen keeps its native top-left; inside it, sizes divide by its scale."""
    if sys.platform != "win32":  # Hyprland: already layout pixels, and the compositor places the window
        return QRect(*rect)
    x, y, w, h = rect
    screens = QGuiApplication.screens()
    s = next((s for s in screens if s.geometry().x() <= x < s.geometry().x() + s.geometry().width() * s.devicePixelRatio()
              and s.geometry().y() <= y < s.geometry().y() + s.geometry().height() * s.devicePixelRatio()),
             QGuiApplication.primaryScreen())
    g, d = s.geometry(), s.devicePixelRatio()
    return QRect(round(g.x() + (x - g.x()) / d), round(g.y() + (y - g.y()) / d), round(w / d), round(h / d))


if __name__ == "__main__":
    from PySide6.QtCore import QTimer
    app = overlay.app()
    g = Grid((200, 200, 120, 120), True) if "marker" in sys.argv else Grid((0, 0, 900, 600))
    QTimer.singleShot(3000, app.quit)
    app.exec()
