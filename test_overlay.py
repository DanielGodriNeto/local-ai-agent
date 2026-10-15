import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

import overlay  # noqa: E402

UI = {"theme": "light", "accent": "blue", "text_scale": 1.0, "font": "",
      "main": {"anchor": "n", "x_pct": 50, "y_pct": 4, "size_max": 30, "size_min": 17, "min_width": 340,
               "max_width_pct": 60, "radius": 26, "pad": 20}}


def ask_in_thread(fn, *args):
    box = []
    t = threading.Thread(target=lambda: box.append(fn(*args)), daemon=True)
    t.start()
    return t, box


def pump_until(cond, timeout=5):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        overlay.app().processEvents()
        time.sleep(0.01)
    assert cond()


def buttons(ov):
    return [b for b in ov.buttons.findChildren(QPushButton) if b.isVisible()]


def test_yes_no_buttons_answer_and_disappear():
    ov = overlay.Overlay(UI)
    t, box = ask_in_thread(ov.ask, "Continuar?")
    pump_until(lambda: [b.text() for b in buttons(ov)] == ["Não", "Sim"])
    buttons(ov)[1].click()
    t.join(2)
    assert box == [True]
    pump_until(lambda: not ov.buttons.isVisible())
    t, box = ask_in_thread(ov.ask, "De novo?")
    pump_until(lambda: len(buttons(ov)) == 2)
    buttons(ov)[0].click()
    t.join(2)
    assert box == [False]
    ov.close()


def test_choice_by_number_key_and_close_answers_none():
    ov = overlay.Overlay(UI)
    t, box = ask_in_thread(ov.choose, "Qual?", ["Chrome", "Firefox"])
    pump_until(lambda: len(buttons(ov)) == 3)  # two options and Cancelar
    ov._armed = 0  # past the arming delay
    ov._key(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier, "2"))
    t.join(2)
    assert box == [1]
    t, box = ask_in_thread(ov.choose, "Qual?", ["Chrome", "Firefox"])
    pump_until(lambda: len(buttons(ov)) == 3)
    ov.close()  # the hotkey or Esc: a pending question gets "no" at once, not after the timeout
    pump_until(lambda: not t.is_alive())
    assert box == [None]
    assert ov.ask("depois de fechada?") is False


def test_long_text_scrolls_inside_a_card_that_fits_the_screen():
    ov = overlay.Overlay(UI)
    ov.text("palavra " * 4000)
    pump_until(lambda: ov.scroll.isVisible())
    area = overlay.QGuiApplication.primaryScreen().availableGeometry()
    assert ov.card.height() <= area.height() * 0.85
    assert ov.body.height() > ov.scroll.height()  # the rest is a scroll away, not cut
    ov.close()


def test_ai_text_is_shown_as_plain_text_never_markup():
    ov = overlay.Overlay(UI)
    ov.text('<b>oi</b><img src="x.png"><a href="http://x">clique</a>')
    pump_until(lambda: ov.body.text().startswith("<b>"))
    assert ov.body.textFormat() == Qt.TextFormat.PlainText and ov.cap.textFormat() == Qt.TextFormat.PlainText
    ov.close()


def test_text_box_sends_on_enter_and_esc_cancels():
    ov = overlay.Overlay(UI)
    t, box = ask_in_thread(ov.prompt, "O que você precisa?")
    pump_until(lambda: isinstance(ov._default, overlay.QLineEdit) and ov._default.isVisible())
    ov._default.setText("  abre o navegador  ")
    ov._default.returnPressed.emit()
    ov._key(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))  # no override
    t.join(2)
    assert box == ["abre o navegador"]
    t, box = ask_in_thread(ov.prompt, "De novo?")
    pump_until(lambda: isinstance(ov._default, overlay.QLineEdit) and ov._default.isVisible())
    ov._key(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    t.join(2)
    assert box == [None]
    ov.close()


def test_corner_pill_shows_one_line_and_opens_on_click():
    ov = overlay.Overlay(UI | {"corner": {**UI["main"], "anchor": "se", "x_pct": 99, "y_pct": 98}})
    answer = "Primeira linha da resposta\n" + "detalhe " * 50
    ov.text(answer)
    ov.to_corner()
    pump_until(lambda: ov.kind == "corner" and ov.body.text().endswith("…"))
    assert "\n" not in ov.body.text() and not ov.closed.is_set()
    ov._click()
    assert ov.kind == "main" and ov.body.text() == answer and not ov.closed.is_set()
    ov._click()
    assert ov.closed.is_set()


def key(ov, k, text=""):
    ov._key(QKeyEvent(QKeyEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text))


def test_keys_typed_as_a_question_pops_up_never_answer_it():
    ov = overlay.Overlay(UI)
    t, box = ask_in_thread(ov.ask, "Enviar a imagem?")
    pump_until(lambda: len(buttons(ov)) == 2)
    key(ov, Qt.Key.Key_Return)  # meant for another window
    t.join(0.2)
    assert t.is_alive() and box == []
    ov._armed = 0
    ov._default.setFocus()
    assert ov._default.text() == "Não"
    ov._default.click()  # what Enter does to the focused button
    t.join(2)
    assert box == [False]
    t, box = ask_in_thread(ov.choose, "Qual?", ["A", "B"])
    pump_until(lambda: len(buttons(ov)) == 3)
    key(ov, Qt.Key.Key_1, "1")
    t.join(0.2)
    assert t.is_alive()
    ov.close()
    t.join(2)
    assert box == [None]


def test_background_question_opens_the_card_without_taking_focus():
    ov = overlay.Overlay(UI | {"corner": {**UI["main"], "anchor": "se", "x_pct": 99, "y_pct": 98}})
    ov.to_corner()
    pump_until(lambda: ov.kind == "corner")
    activated = []
    ov.win.activateWindow = lambda: activated.append(1)
    t, box = ask_in_thread(ov.ask, "Continuar?")
    pump_until(lambda: ov.kind == "main" and len(buttons(ov)) == 2)
    assert not activated
    ov.close()
    t.join(2)
