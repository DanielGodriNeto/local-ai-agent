import numpy as np

import intent

VOCAB = ["navegador", "internet", "terminal", "console", "tela", "trava", "capital", "bug", "codigo", "jogar"]


def fake_embed(texts):
    """Bag of words over VOCAB: deterministic, no model needed."""
    out = np.array([[float(w in t.lower()) for w in VOCAB] for t in texts]) + 1e-6
    return out / np.linalg.norm(out, axis=1, keepdims=True)


PRESETS = {
    "open_browser": {"examples": ["abre o navegador", "internet"]},
    "open_terminal": {"examples": ["abre o terminal", "console"]},
    "lock_screen": {"examples": ["trava a tela"]},
}


def matcher(**kw):
    builtin = {"quick": ["qual a capital"], "task": ["corrige o bug no codigo"]}
    return intent.Matcher(PRESETS, embed=fake_embed, builtin=builtin, **kw)


def test_clear_preset_runs():
    assert matcher(min_score=0.5, margin=0.1).decide("abre o navegador") == {"action": "run_preset", "preset": "open_browser"}


def test_builtin_classes_escalate_with_mode():
    m = matcher(min_score=0.5, margin=0.1)
    assert m.decide("qual a capital da franca") == {"action": "escalate", "mode": "quick", "task": "qual a capital da franca"}
    assert m.decide("tem um bug no codigo")["mode"] == "task"


def test_low_score_escalates_instead_of_guessing():
    assert matcher(min_score=0.99, margin=0.1).decide("navegador")["action"] == "run_preset"
    assert matcher(min_score=0.99, margin=0.1).decide("navegador terminal")["action"] != "run_preset"


def test_two_close_presets_ask_the_user():
    d = matcher(min_score=0.1, margin=0.1).decide("navegador ou terminal")
    assert d["action"] == "choose" and set(d["options"]) == {"open_browser", "open_terminal"}


def test_opposing_verb_never_runs_a_preset():
    m = matcher(min_score=0.1, margin=0.01)
    for text in ("fecha o navegador", "desliga o terminal", "para o terminal", "pare o navegador",
                 "pode parar o terminal", "encerra a internet", "cancela o terminal",
                 "não abre o navegador", "nao abre o terminal", "nunca abre o navegador", "não quero abrir o terminal",
                 "don't open the terminal", "deixa de abrir o terminal", "pára o terminal", "sai do navegador",
                 "sair do terminal", "exit the terminal", "tira o navegador", "remove o terminal"):
        assert m.decide(text)["action"] == "escalate", text


def test_para_as_preposition_is_not_a_stop_verb():
    assert intent.opposing("abre o navegador para mim") is False
    assert intent.opposing("abre um site pra mim") is False
    assert intent.opposing("abre o terminal") is False  # "termin..." must not catch "terminal"
    assert intent.opposing("para o jogo") is True
    assert intent.opposing("jarvis, fecha isso") is True


def test_empty_text_clarifies():
    assert matcher().decide("   ")["action"] == "clarify"


def test_question_about_an_action_is_not_the_action():
    m = matcher(min_score=0.1, margin=0.01)
    for text in ("como eu abro o terminal no linux", "por que meu navegador trava?", "jarvis, qual internet é melhor"):
        assert m.decide(text) == {"action": "escalate", "mode": "quick", "task": text}, text
    assert m.decide("abre o terminal")["action"] == "run_preset"


def test_fetch_model_rejects_a_tampered_download(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr("urllib.request.urlretrieve", lambda url, dest: dest.write_bytes(b"evil"))
    with pytest.raises(RuntimeError, match="checksum"):
        intent.fetch_model(tmp_path)
    assert not any(tmp_path.rglob("*.onnx"))  # the bad file is deleted, never loaded
