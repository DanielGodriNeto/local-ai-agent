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
