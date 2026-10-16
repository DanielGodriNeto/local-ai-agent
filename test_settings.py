import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox  # noqa: E402

import overlay  # noqa: E402
import settings  # noqa: E402


def test_every_settings_page_builds_and_offers_the_quick_question_choice(monkeypatch):
    overlay.app()
    monkeypatch.setattr(settings.config, "save", lambda change: None)  # never touch the real config.json
    w = settings.Settings()
    quick = [b for b in w.findChildren(QComboBox) if b.count() and b.itemText(0).startswith("No PC")]
    assert len(quick) == 1 and quick[0].count() == 2
    quick[0].setCurrentIndex(1)
    assert w.cfg["brain"]["quick"] == "main"
