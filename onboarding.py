"""First run: what the assistant found on this PC, and the models it needs.
The daemon opens it once; run it again any time: python onboarding.py
"""
import sys
import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import config
import overlay
import settings


def brain_status(cfg: dict) -> str:
    import brains
    name = brains.pick(cfg)
    if not name:
        return "Nenhuma: instale Claude Code, Codex ou Ollama"
    return {"claude": "Claude Code", "codex": "Codex", "ollama": "Ollama"}.get(name, name)


def mic_status() -> str:
    try:
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"]
    except Exception:  # noqa: BLE001 - no device, no driver, no PortAudio: all mean the same here
        return "Nenhum microfone encontrado"


def models(cfg: dict) -> list[tuple[str, Callable[[], bool], Optional[Callable[[], None]], str]]:
    """(name, ready?, download or None, note when it cannot be downloaded here)."""
    import intent
    import tts
    import wake
    v = cfg["voice"]

    def has_kokoro() -> bool:
        try:
            import kokoro_onnx  # noqa: F401
            return True
        except ImportError:
            return False

    def files(d, f):
        return lambda: all((d / name).exists() for name in f)

    import listen
    return [
        ("Entender comandos · 120 MB", files(intent.MODEL_DIR, intent.MODEL_FILES), intent.fetch_model, ""),
        (f"Transcrição de voz · {v['model']}", lambda: listen.whisper_ready(v["model"]),
         lambda: listen.whisper_path(v["model"]), ""),
        ("Palavra de ativação · 4 MB", files(wake.MODEL_DIR, wake.MODEL_FILES),
         lambda: intent.fetch_model(wake.MODEL_DIR, wake.MODEL_FILES), ""),
        ("Voz falada · 340 MB · opcional", tts.available,
         (lambda: intent.fetch_model(tts.MODEL_DIR, tts.MODEL_FILES)) if has_kokoro() else None,
         "pip install kokoro-onnx"),
    ]


class Onboarding(QWidget):
    def __init__(self):
        overlay.app()
        super().__init__()
        self.cfg = config.load()
        self.gui = overlay.Bridge(self)
        self.setWindowTitle("Bem-vindo")
        self.resize(640, 560)
        col = QVBoxLayout(self)
        col.setContentsMargins(32, 28, 32, 24)
        col.setSpacing(12)
        head = QLabel("Seu assistente, no seu PC")
        head.setObjectName("title")
        col.addWidget(head)
        col.addWidget(settings.note("Fale com Win+F12 ou dizendo “Hey Jarvis”; escreva com Win+F11. Ele usa a IA que "
                                    "você já tem, sem chave de API, e o áudio nunca sai do PC."))
        col.addWidget(settings.group([("Inteligência", QLabel(brain_status(self.cfg))),
                                      ("Microfone", QLabel(mic_status()))]))
        col.addWidget(settings.group([(name, self._model_row(ready, fetch, hint))
                                      for name, ready, fetch, hint in models(self.cfg)]))
        col.addWidget(settings.note("Todos os modelos vêm de versões fixas, conferidas por sha256."))
        col.addStretch()
        bar = QHBoxLayout()
        bar.addStretch()
        go = QPushButton("Começar")
        go.setDefault(True)
        go.clicked.connect(self._finish)
        bar.addWidget(go)
        col.addLayout(bar)
        p = overlay.palette(self.cfg["ui"])
        self.setStyleSheet(settings.style(p) + f"QPushButton:default {{ background: {p['accent']}; color: white; "
                           "border: none; padding: 7px 20px; font-weight: 600; }")

    def _model_row(self, ready: Callable[[], bool], fetch: Optional[Callable[[], None]], hint: str) -> QWidget:
        holder, line = QWidget(), QHBoxLayout()
        holder.setLayout(line)
        line.setContentsMargins(0, 0, 0, 0)
        state = QLabel()
        state.setTextFormat(Qt.TextFormat.PlainText)  # error text from the network is never markup
        line.addWidget(state)
        if ready():
            state.setText("Pronto")
            return holder
        if fetch is None:
            state.setText(hint)
            return holder
        button = QPushButton("Baixar")
        line.addWidget(button)

        def done(error: str) -> None:
            button.setVisible(bool(error))
            button.setEnabled(True)
            state.setText(f"Falhou: {error[:60]}" if error else "Pronto")

        def work() -> None:
            try:
                fetch()
                error = ""
            except Exception as e:  # noqa: BLE001 - shown on the row; the user can retry
                error = str(e) or type(e).__name__
            self.gui.call.emit(lambda: done(error))

        def start() -> None:
            button.setEnabled(False)
            state.setText("Baixando…")
            threading.Thread(target=work, daemon=True).start()
        button.clicked.connect(start)
        return holder

    def _finish(self) -> None:
        self.close()

    def closeEvent(self, e) -> None:  # closed either way: do not open again on the next start
        config.save({"onboarded": True})
        super().closeEvent(e)


if __name__ == "__main__":
    app = overlay.app()
    app.setStyle("Fusion")
    w = Onboarding()
    w.show()
    sys.exit(app.exec())
