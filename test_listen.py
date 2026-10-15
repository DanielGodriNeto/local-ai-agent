import numpy as np

import listen as L

N = int(L.RATE * L.BLOCK_S)
SILENCE = np.zeros(N, dtype=np.float32)
TONE = (0.2 * np.sin(np.linspace(0, 400, N))).astype(np.float32)
CLICK = TONE.copy()


def feed_all(seg, blocks):
    return [seg.feed(b) for b in blocks]


def test_segmenter_start_then_end_after_silence():
    ev = feed_all(L.Segmenter(silence_s=1.0), [SILENCE] * 3 + [TONE] * 5 + [SILENCE] * 12)
    assert ev.count("start") == 1 and ev[-1] == "end"
    assert ev.index("start") == 4  # needs two loud blocks


def test_single_click_is_not_speech_and_silence_times_out():
    seg = L.Segmenter(no_speech_s=1.0)
    ev = feed_all(seg, [SILENCE, CLICK] + [SILENCE] * 12)
    assert "start" not in ev and ev[-1] == "timeout"


def test_pause_shorter_than_silence_does_not_end():
    ev = feed_all(L.Segmenter(silence_s=1.0), [TONE] * 3 + [SILENCE] * 5 + [TONE] * 3)
    assert "end" not in ev


def test_max_length_forces_end():
    assert "end" in feed_all(L.Segmenter(max_s=1.0), [TONE] * 30)


def test_listen_returns_final_text_and_includes_preroll():
    lengths = []

    def fake_stt(audio):
        lengths.append(len(audio))
        return " abre o navegador "

    blocks = iter([SILENCE] * 4 + [TONE] * 5 + [SILENCE] * 12)
    assert L.listen(blocks, L.Segmenter(), fake_stt, lambda _t: None) == "abre o navegador"
    assert lengths[-1] > 5 * N  # speech + pre-roll + trailing silence reached the final pass


def test_listen_none_when_nothing_said():
    assert L.listen(iter([SILENCE] * 80), L.Segmenter(no_speech_s=2.0), lambda _a: "x", lambda _t: None) is None


ENERGY_CFG = {**L.o.load_config()}
ENERGY_CFG["voice"] = {**ENERGY_CFG["voice"], "vad": "energy", "earcons": False}  # sine test tones are not speech


class FakeUI:
    def __init__(self):
        import threading
        self.closed, self.texts, self.holds = threading.Event(), [], []

    def status(self, *_a):
        pass

    def text(self, s, *_a):
        self.texts.append(s)

    def hold(self, seconds):
        self.holds.append(seconds)

    def close(self):
        self.closed.set()


def test_long_answer_is_kept_whole_streamed_and_stays_up(monkeypatch):
    long = "palavra " * 625  # 5,000 characters

    def handle(text, cfg, on_event=None, **_kw):
        for i in range(0, len(long), 1000):
            on_event(type("E", (), {"kind": "text", "text": long[i:i + 1000]})())
        return long

    monkeypatch.setattr(L.o, "handle", handle)
    ui, blocks = FakeUI(), iter([SILENCE] * 2 + [TONE] * 5 + [SILENCE] * 12)
    L.session(ui, ENERGY_CFG, blocks, models=lambda: (lambda _a: "conta uma historia", None))
    assert ui.texts[-1] == long.strip() and L.HISTORY[-1] == ("conta uma historia", long.strip())
    assert len(ui.texts[-2]) == 5000  # the streamed text grew to the full answer
    assert ui.holds == [None]  # stays until dismissed


def test_short_answer_fades():
    ui = FakeUI()
    L.o.handle, real = (lambda *a, **k: "preset: open_browser"), L.o.handle
    try:
        L.session(ui, ENERGY_CFG, iter([TONE] * 5 + [SILENCE] * 12), models=lambda: (lambda _a: "abre", None))
    finally:
        L.o.handle = real
    assert ui.holds == [4]


def speech_wav(path):
    """Real speech without shipping a recording: Windows' built-in voice reads a sentence (skipped elsewhere)."""
    import subprocess
    import sys

    import pytest
    if sys.platform != "win32":
        pytest.skip("needs the Windows speech synthesizer")
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 16, 1); "
          f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak('Abre o navegador, por favor. Qual a capital da Franca?')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
    return path


def test_silero_hears_speech_not_tones_or_noise():
    import wave
    from pathlib import Path
    vad = L.SileroVAD()
    rng = np.random.default_rng(0)
    assert max(vad(TONE) for _ in range(10)) < 0.5
    assert max(vad((rng.standard_normal(N) * 0.05).astype(np.float32)) for _ in range(10)) < 0.5
    speech = speech_wav(Path(__import__("tempfile").mkdtemp()) / "fala.wav")
    with wave.open(str(speech)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    seg = L.Segmenter(vad=L.SileroVAD())
    events = [seg.feed(audio[i:i + N]) for i in range(0, len(audio) - N, N)] + [seg.feed(SILENCE) for _ in range(12)]
    assert events.count("start") == 1 and "end" in events


def test_earcons_are_short_and_never_raise(monkeypatch):
    import earcons
    assert all(0.05 < len(c) / earcons.RATE < 0.4 and abs(c).max() <= 0.3 for c in earcons.CUES.values())
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", None)  # no audio device / import fails
    earcons.play("done")


def test_typed_session_skips_the_mic_and_a_long_task_moves_to_the_corner(monkeypatch):
    import threading
    calls, release = [], threading.Event()

    class TypedUI(FakeUI):
        def prompt(self, _q):
            return "refatora o modulo"

        def to_corner(self):
            calls.append("corner")
            release.set()  # the task finishes only after it moved

    def handle(text, cfg, **_kw):
        calls.append(text)
        release.wait(5)
        return "Refatorei."

    monkeypatch.setattr(L.o, "handle", handle)
    cfg = {**ENERGY_CFG, "ui": {**ENERGY_CFG["ui"], "background_after_s": 0.05, "notify": True}}
    ui = TypedUI()
    L.session(ui, cfg, iter([]), models=lambda: (lambda _a: "nunca usado", None), typed=True,
              detach=lambda: calls.append("detach"), notify=lambda title, text: calls.append((title, text)))
    assert calls == ["refatora o modulo", "detach", "corner", ("Tarefa pronta", "Refatorei.")]
    assert ui.texts[-1] == "Refatorei." and ui.holds == [None]  # waits in the pill until opened


def test_typed_session_closes_on_empty_text():
    class EmptyUI(FakeUI):
        def prompt(self, _q):
            return None

    ui = EmptyUI()
    L.session(ui, ENERGY_CFG, iter([]), models=lambda: (lambda _a: "x", None), typed=True)
    assert ui.closed.is_set() and ui.holds == []


def test_typed_grid_session_uses_the_text_box_for_each_round_and_never_the_ai(monkeypatch):
    from test_access import FakeDesk
    answers, desk = ["mostrar grade", "5", "clica"], FakeDesk()

    class GridUI(FakeUI):
        gui = win = None

        def prompt(self, _q):
            return answers.pop(0)

    monkeypatch.setattr(L.o, "handle", lambda *a, **k: (_ for _ in ()).throw(AssertionError("went to the AI")))
    monkeypatch.setattr(L.access, "Desk", lambda *_a: desk)
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.access.orchestrator, "log", lambda entry: None)
    ui = GridUI()
    L.session(ui, ENERGY_CFG, iter([]), models=lambda: (lambda _a: "x", None), typed=True,
              detach=lambda: (_ for _ in ()).throw(AssertionError("moved to the background")))
    assert desk.clicks == [((450, 450), "left")] and ui.texts[-1] == "Cliquei."
