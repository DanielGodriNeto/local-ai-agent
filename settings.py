"""Settings, laid out like macOS System Settings: a sidebar of sections, each a page of grouped rows.
Every change is saved at once to config.json (git-ignored). Run: python settings.py
"""
import sys
from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget)

import config
import overlay

LANGUAGES = {"pt": "Português", "en": "English", "es": "Español", "fr": "Français", "it": "Italiano",
             "hi": "हिन्दी", "ja": "日本語", "zh": "中文"}
BRAINS = {"auto": "Automático (o que estiver instalado)", "claude": "Claude Code", "codex": "Codex",
          "ollama": "Ollama (só local)"}
THEMES = {"auto": "Automático", "light": "Claro", "dark": "Escuro"}
POSITIONS = {"Topo": ("n", 50, 4), "Centro": ("c", 50, 45), "Base": ("s", 50, 96)}
CORNERS = {"Inferior direito": ("se", 99, 98), "Inferior esquerdo": ("sw", 1, 98),
           "Superior direito": ("ne", 99, 2), "Superior esquerdo": ("nw", 1, 2)}
SAMPLES = {"main": "abre o navegador e procura receitas", "corner": "Editando orchestrator.py"}


class Switch(QAbstractButton):
    """On/off switch (a checkable button, so keyboard Space and screen readers work as for a checkbox)."""

    def __init__(self, accent: str):
        super().__init__()
        self.accent = accent
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(40, 24)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1, 1, 38, 22)
        p.setPen(QColor(self.accent) if self.hasFocus() else Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.accent) if self.isChecked() else QColor(120, 120, 128, 80))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(19 if self.isChecked() else 3, 3, 18, 18))


def style(p: dict) -> str:
    window = "#ececec" if p["fg"] == "#1d1d1f" else "#1e1e1e"
    group = "#ffffff" if p["fg"] == "#1d1d1f" else "#2a2a2c"
    line = "rgba(%d,%d,%d,%d)" % p["line"]
    return f"""
        QWidget {{ background: {window}; color: {p['fg']}; font-size: 13px; }}
        QListWidget {{ border: none; padding: 8px; outline: none; }}
        QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}
        QListWidget::item:selected {{ background: {p['accent']}; color: white; }}
        QLabel#title {{ font-size: 22px; font-weight: 600; }}
        QLabel#note {{ color: {p['muted']}; font-size: 12px; }}
        QFrame#group {{ background: {group}; border: 1px solid {line}; border-radius: 10px; }}
        QFrame#group QWidget {{ background: transparent; }}
        QFrame#group QFrame#sep {{ background: {line}; max-height: 1px; border: none; }}
        QComboBox {{ background: {group}; border: 1px solid {line}; border-radius: 6px; padding: 3px 8px;
                     min-width: 170px; }}
        QPushButton {{ background: {group}; border: 1px solid {line}; border-radius: 6px; padding: 5px 14px; }}
        QPushButton:focus, QComboBox:focus {{ border: 2px solid {p['accent']}; }}
        QPushButton#swatch {{ border-radius: 11px; min-width: 22px; max-width: 22px; min-height: 22px;
                              max-height: 22px; padding: 0; }}
        QSlider::groove:horizontal {{ height: 4px; background: {line}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: white; border: 1px solid rgba(0,0,0,60); width: 18px; height: 18px;
                                      margin: -8px 0; border-radius: 9px; }}
    """


class Settings(QWidget):
    def __init__(self):
        overlay.app()
        super().__init__()
        self.cfg = config.load()
        self.setWindowTitle("Ajustes do Assistente")
        self.resize(720, 520)
        self.side = QListWidget()
        self.side.setFixedWidth(190)
        self.side.setAccessibleName("Seções")
        self.pages = QStackedWidget()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.side)
        row.addWidget(self.pages, 1)
        for name, build in (("Geral", self._general), ("Voz", self._voice), ("Aparência", self._look)):
            self.side.addItem(name)
            self.pages.addWidget(self._page(name, build()))
        self.side.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.side.setCurrentRow(0)
        self._restyle()

    # ---- building blocks ----
    def _page(self, title: str, parts: list) -> QWidget:
        page, col = QWidget(), QVBoxLayout()
        page.setLayout(col)
        col.setContentsMargins(28, 22, 28, 22)
        col.setSpacing(10)
        head = QLabel(title)
        head.setObjectName("title")
        col.addWidget(head)
        for part in parts:
            col.addWidget(part)
        col.addStretch()
        return page

    def _group(self, rows: list[tuple[str, QWidget]]) -> QFrame:
        box = QFrame()
        box.setObjectName("group")
        col = QVBoxLayout(box)
        col.setContentsMargins(14, 4, 14, 4)
        col.setSpacing(0)
        for i, (label, control) in enumerate(rows):
            if i:
                sep = QFrame()
                sep.setObjectName("sep")
                col.addWidget(sep)
            line = QHBoxLayout()
            line.setContentsMargins(0, 8, 0, 8)
            name = QLabel(label)
            name.setBuddy(control)
            control.setAccessibleName(label)
            line.addWidget(name, 1)
            line.addWidget(control)
            col.addLayout(line)
        return box

    def _note(self, text: str) -> QLabel:
        n = QLabel(text)
        n.setObjectName("note")
        n.setWordWrap(True)
        return n

    def _combo(self, options: dict, current: str, on: Callable[[str], None]) -> QComboBox:
        box = QComboBox()
        for key, label in options.items():
            box.addItem(label, key)
        box.setCurrentIndex(max(0, box.findData(current)))
        box.currentIndexChanged.connect(lambda _i: on(box.currentData()))
        return box

    def _check(self, value: bool, on: Callable[[bool], None]) -> Switch:
        box = Switch(overlay.palette(self.cfg["ui"])["accent"])
        box.setChecked(value)
        box.toggled.connect(on)
        return box

    def _slider(self, lo: int, hi: int, value: float, on: Callable[[float], None]) -> QWidget:
        holder, line = QWidget(), QHBoxLayout()
        holder.setLayout(line)
        line.setContentsMargins(0, 0, 0, 0)
        s, shown = QSlider(Qt.Orientation.Horizontal), QLabel()
        s.setRange(lo, hi)
        s.setValue(round(value * 100))
        s.setFixedWidth(170)
        shown.setFixedWidth(42)
        shown.setText(f"{s.value()}%")
        s.valueChanged.connect(lambda v: (shown.setText(f"{v}%"), on(v / 100)))
        line.addWidget(s)
        line.addWidget(shown)
        return holder

    def _save(self, section: str, key: str, value) -> None:
        self.cfg[section][key] = value
        config.save({section: {key: value}})

    # ---- pages ----
    def _general(self) -> list:
        b, d = self.cfg["brain"], self.cfg["daemon"]
        return [
            self._group([
                ("Inteligência", self._combo(BRAINS, b["use"], lambda v: self._save("brain", "use", v))),
                ("Memória", self._combo({"leve": "Leve (libera após 5 min parado)", "rapido": "Rápida (sempre pronta)"},
                                        d.get("mode", "leve"), lambda v: self._save("daemon", "mode", v))),
            ]),
            self._note("Nenhuma chave de API: o assistente usa o login que você já tem no Claude Code ou Codex, "
                       "ou um modelo Ollama no seu PC. Memória vale depois de reiniciar o assistente."),
        ]

    def _voice(self) -> list:
        v, w = self.cfg["voice"], self.cfg["wake"]
        return [
            self._group([
                ("Idioma", self._combo(LANGUAGES, v["language"], lambda x: self._save("voice", "language", x))),
                ("Falar as respostas", self._check(v.get("tts", True), lambda x: self._save("voice", "tts", x))),
                ("Velocidade da fala", self._slider(70, 140, v.get("speed", 1.0),
                                                    lambda x: self._save("voice", "speed", round(x, 2)))),
                ("Sons de aviso", self._check(v.get("earcons", True), lambda x: self._save("voice", "earcons", x))),
            ]),
            self._group([
                (f"Ativar dizendo “{w['phrase']}”", self._check(w["enabled"],
                                                                 lambda x: self._save("wake", "enabled", x))),
            ]),
            self._note('Outra frase: python wake.py --record "sua frase". Voz e ativação valem depois de reiniciar '
                       "o assistente. O áudio nunca sai do PC."),
        ]

    def _look(self) -> list:
        ui = self.cfg["ui"]
        swatches, group = QWidget(), QButtonGroup(self)
        line = QHBoxLayout(swatches)
        line.setContentsMargins(0, 0, 0, 0)
        for name, (light, _dark) in overlay.ACCENTS.items():
            b = QPushButton()
            b.setObjectName("swatch")
            b.setCheckable(True)
            b.setChecked(name == ui.get("accent", "blue"))
            b.setAccessibleName(name)
            b.setToolTip(name)
            b.setStyleSheet(f"QPushButton#swatch {{ background: {light}; }} "
                            "QPushButton#swatch:checked { border: 3px solid rgba(128,128,128,0.6); }")
            b.clicked.connect(lambda _=False, n=name: self._look_set("accent", n))
            group.addButton(b)
            line.addWidget(b)
        where = next((k for k, v in POSITIONS.items() if v[0] == ui["main"]["anchor"]), "Topo")
        corner = next((k for k, v in CORNERS.items() if v[0] == ui["corner"]["anchor"]), "Inferior direito")
        preview = QPushButton("Mostrar exemplo")
        preview.clicked.connect(self._preview)
        return [
            self._group([
                ("Tema", self._combo(THEMES, ui.get("theme", "auto"), lambda x: self._look_set("theme", x))),
                ("Cor de destaque", swatches),
                ("Tamanho do texto", self._slider(80, 160, ui.get("text_scale", 1.0),
                                                  lambda x: self._look_set("text_scale", round(x, 2)))),
            ]),
            self._group([
                ("Janela principal", self._combo({k: k for k in POSITIONS}, where,
                                                 lambda x: self._place("main", POSITIONS[x]))),
                ("Atividade em andamento", self._combo({k: k for k in CORNERS}, corner,
                                                       lambda x: self._place("corner", CORNERS[x]))),
                ("", preview),
            ]),
            self._note("Respeita o modo de alto contraste e a opção “Mostrar animações” do sistema."),
        ]

    def _look_set(self, key: str, value) -> None:
        self._save("ui", key, value)
        self._restyle()

    def _place(self, kind: str, spot: tuple) -> None:
        anchor, x, y = spot
        self.cfg["ui"][kind].update(anchor=anchor, x_pct=x, y_pct=y)
        config.save({"ui": {kind: {"anchor": anchor, "x_pct": x, "y_pct": y}}})
        self._preview()

    def _preview(self) -> None:
        import threading
        for kind in ("main", "corner"):
            ov = overlay.Overlay(self.cfg["ui"], kind)
            ov.status("Ouvindo" if kind == "main" else "", overlay.LISTEN)
            ov.text(SAMPLES[kind])
            threading.Thread(target=ov.hold, args=(3,), daemon=True).start()

    def _restyle(self) -> None:
        self.setStyleSheet(style(overlay.palette(self.cfg["ui"])))


if __name__ == "__main__":
    app = overlay.app()
    app.setStyle("Fusion")
    s = Settings()
    s.show()
    sys.exit(app.exec())
