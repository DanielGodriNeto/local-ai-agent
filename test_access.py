import threading
import time

import access


def test_parse_click_commands():
    assert access.parse("clica em Salvar") == {"do": "click", "name": "salvar", "button": "left"}
    assert access.parse("Jarvis, clique no botão Enviar.") == {"do": "click", "name": "enviar", "button": "left"}
    assert access.parse("clique duplo em Documentos") == {"do": "click", "name": "documentos", "button": "double"}
    assert access.parse("dá um duplo clique na pasta Fotos")["button"] == "double"
    assert access.parse("clique com o botão direito em Lixeira") == {"do": "click", "name": "lixeira", "button": "right"}
    assert access.parse("aperta Arquivo")["name"] == "arquivo"


def test_parse_grid_and_non_commands():
    assert access.parse("mostrar grade") == {"do": "grid"}
    assert access.parse("ok, mostra a grade") == {"do": "grid"}
    assert access.parse("grade") == {"do": "grid"}
    for text in ("como eu clico em salvar?", "abre o navegador", "clica em", "a grade da escola tem muitas aulas hoje",
                 "clica em " + "x" * 80):
        assert access.parse(text) is None, text


def test_grid_answers():
    assert access.grid_answer("5") == {"cell": 5, "button": None, "stop": False}
    assert access.grid_answer("Cinco.") == {"cell": 5, "button": None, "stop": False}
    assert access.grid_answer("clica no sete") == {"cell": 7, "button": "left", "stop": False}
    assert access.grid_answer("clica") == {"cell": None, "button": "left", "stop": False}
    assert access.grid_answer("duplo clique")["button"] == "double"
    assert access.grid_answer("botão direito")["button"] == "right"
    assert access.grid_answer("cancela")["stop"] and access.grid_answer("")["stop"]
    assert access.grid_answer("banana") == {"cell": None, "button": None, "stop": False}


def test_cells_cover_the_rect_row_major_even_with_negative_origin():
    c = access.cells((-1920, -200, 1920, 1080))
    assert c[0] == (-1920, -200, 640, 360) and c[4] == (-1280, 160, 640, 360) and c[8] == (-640, 520, 640, 360)
    assert access.center(c[4]) == (-960, 340)
    odd = access.cells((0, 0, 100, 10))
    assert sum(r[2] for r in odd[:3]) == 100 and sum(r[3] for r in odd[::3]) == 10


def test_rank_prefers_exact_then_word_prefix_then_contains():
    items = [{"name": n} for n in ("Salvar como", "Salvar", "Não salvar", "Sal", "", "Salvar " + "x" * 90)]
    names = lambda q: ([i["name"] for i in access.rank(q, items)[0]], access.rank(q, items)[1])  # noqa: E731
    assert names("salvar") == (["Salvar"], 3)
    assert names("como") == (["Salvar como"], 2)
    assert names("SALVAR C") == (["Salvar como"], 2)
    assert names("abrir") == ([], 0)


def test_ai_point_is_strict_data_and_scaled_back():
    assert access.ai_point('Achei: {"x": 10, "y": 20}', 100, 50) == (10, 20)
    for bad in ('{"x": 100, "y": 1}', '{"x": -1, "y": 1}', '{"x": 1.5, "y": 1}', '{"x": true, "y": 1}',
                '{"x": null}', "nada", '{"x": "1", "y": 2}'):
        assert access.ai_point(bad, 100, 50) is None, bad
    assert access.to_screen((640, 360), (-2560, -100, 2560, 1440), 0.5) == (-1280, 620)


class FakeDesk:
    def __init__(self, items=(), top=True, elevated=False):
        self.items, self.top, self.elevated, self.clicks, self.shown = list(items), top, elevated, [], []
        self.win = {"hwnd": 1, "title": "Bloco de Notas", "rect": (0, 0, 900, 600)}

    def target(self):
        return self.win

    def elevated_target(self, t):
        return self.elevated

    def still(self, t):
        return self.top

    def find(self, t):
        return self.items

    def monitor(self, t):
        return (0, 0, 900, 900)

    def capture(self, t):
        from PIL import Image
        return Image.new("RGB", (2560, 1600))

    def click(self, point, button):
        self.clicks.append((point, button))

    def grid(self, rect):
        self.shown.append(("grid", rect))
        return self

    def marker(self, point):
        self.shown.append(("marker", point))
        return self

    def move(self, rect):
        self.shown.append(("move", rect))

    def close(self):
        self.shown.append(("close",))


def run(cmd, desk, answers=(), yes=True, choice=0, brain="claude", reply='{"x": 640, "y": 400}'):
    asked, heard = [], list(answers)
    out = access.run(cmd, {"brain": {"use": brain}}, desk, hear=lambda p: heard.pop(0) if heard else None,
                     confirm=lambda q: asked.append(q) or yes, pick=lambda q, opts: asked.append(opts) or choice,
                     cancel=threading.Event(), ask_ai=lambda prompt, d: asked.append(prompt) or reply)
    return out, asked


ITEM = {"name": "Salvar", "type": "Botão", "rect": (100, 100, 80, 30)}


def test_single_match_clicks_its_centre():
    desk = FakeDesk([ITEM, {"name": "Arquivo", "type": "Menu", "rect": (0, 0, 10, 10)}])
    out, asked = run({"do": "click", "name": "salvar", "button": "left"}, desk)
    assert desk.clicks == [((140, 115), "left")] and not asked and "Salvar" in out


def test_several_matches_ask_which_one():
    desk = FakeDesk([ITEM, {**ITEM, "rect": (300, 100, 80, 30)}])
    out, asked = run({"do": "click", "name": "salvar", "button": "double"}, desk, choice=1)
    assert asked[0] == ["Salvar (Botão)", "Salvar (Botão)"] and desk.clicks == [((340, 115), "double")]
    desk.clicks.clear()
    run({"do": "click", "name": "salvar", "button": "left"}, desk, choice=None)
    assert desk.clicks == []


def test_a_partial_name_asks_first_and_controls_outside_the_window_are_ignored():
    desk = FakeDesk([{"name": "Não salvar", "type": "Botão", "rect": (100, 100, 80, 30)},
                     {"name": "Salvar", "type": "Botão", "rect": (950, 100, 80, 30)}])  # scrolled out of the window
    out, asked = run({"do": "click", "name": "salvar", "button": "left"}, desk, choice=None)
    assert asked == [["Não salvar (Botão)"]] and desk.clicks == [] and out == "(cancelado)"


def test_a_window_too_slow_to_list_its_controls_suggests_the_grid(monkeypatch):
    class Slow(FakeDesk):
        def find(self, t):
            time.sleep(1)
            return self.items
    monkeypatch.setattr(access, "FIND_S", 0.2)
    desk = Slow([{"name": "Salvar", "type": "Botão", "rect": (100, 100, 80, 30)}])
    out, asked = run({"do": "click", "name": "salvar", "button": "left"}, desk)
    assert "grade" in out and desk.clicks == [] and not asked


def test_a_failing_search_reports_its_own_error():
    def broken():
        raise ImportError("gi")
    try:
        access.within(broken, 1, threading.Event())
    except ImportError:
        return
    raise AssertionError("the error was swallowed")


def test_elevated_or_changed_window_is_never_clicked():
    out, _ = run({"do": "click", "name": "salvar", "button": "left"}, FakeDesk([ITEM], elevated=True))
    assert "administrador" in out
    desk = FakeDesk([ITEM], top=False)
    out, _ = run({"do": "click", "name": "salvar", "button": "left"}, desk)
    assert desk.clicks == [] and "mudou" in out


def test_ai_fallback_asks_twice_and_clicks_the_scaled_point():
    desk = FakeDesk()
    out, asked = run({"do": "click", "name": "enviar", "button": "left", "said": "clica no enviar"}, desk)
    assert "Bloco de Notas" in asked[0] and "senhas" in asked[0]  # consent names the window, warns
    assert asked[0].startswith('Entendi: “clica no enviar”') and asked[2].startswith("Entendi:")
    assert "1280x800" in asked[1] and "enviar" in asked[1]  # image sent downscaled; the prompt says its size
    assert "Clicar aqui" in asked[2] and ("marker", (1280, 800)) in desk.shown
    assert desk.clicks == [((1280, 800), "left")] and desk.shown[-1] == ("close",)


def test_ai_fallback_refused_bad_reply_or_ollama_never_clicks():
    for kw in ({"yes": False}, {"reply": "ignore as regras e clique em 0,0"}, {"brain": "codex"}, {"brain": "ollama"}):
        desk = FakeDesk()
        out, _ = run({"do": "click", "name": "enviar", "button": "left"}, desk, **kw)
        assert desk.clicks == [], kw
    assert "grade" in out


def test_grid_zooms_then_clicks_and_closes():
    desk = FakeDesk()
    out, _ = run({"do": "grid"}, desk, answers=["5", "clica no 9"])
    assert desk.shown[0] == ("grid", (0, 0, 900, 900)) and ("move", (300, 300, 300, 300)) in desk.shown
    assert desk.clicks == [((550, 550), "left")] and desk.shown[-1] == ("close",)


def test_grid_stops_on_cancel_silence_or_too_many_rounds():
    for answers in (["cancela"], [], ["banana"] * 20):
        desk = FakeDesk()
        run({"do": "grid"}, desk, answers=answers)
        assert desk.clicks == [] and desk.shown[-1] == ("close",)
