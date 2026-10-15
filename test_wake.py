import shutil
import subprocess
import sys
import threading
import wave

import numpy as np
import pytest

import wake

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="test speech comes from the Windows speech synthesizer"),
    pytest.mark.skipif(not all((wake.MODEL_DIR / f).exists() for f in wake.MODEL_FILES), reason="python wake.py"),
]


def say(tmp_path, text, voice="Microsoft David Desktop", rate=0):
    """Speech without shipping recordings: Windows' built-in voices, padded with a second of silence."""
    path = tmp_path / f"{abs(hash((text, voice, rate)))}.wav"
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.Rate = {rate}; "
          "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 16, 1); "
          f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak('{text}')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
    with wave.open(str(path)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    pad = np.zeros(wake.RATE, np.float32)
    return np.concatenate([pad, audio, pad])


def best(audio, score, model_dir=wake.MODEL_DIR):
    feats = wake.Features(model_dir)
    return max(score(feats(audio[i:i + wake.CHUNK])) for i in range(0, len(audio) - wake.CHUNK + 1, wake.CHUNK))


def test_hey_jarvis_wakes_and_lookalikes_do_not(tmp_path):
    assert best(say(tmp_path, "Hey Jarvis"), wake.Jarvis()) > 0.9
    assert best(say(tmp_path, "Hey Jarvis", "Microsoft Zira Desktop"), wake.Jarvis()) > 0.9
    for text in ("Hey, how are you doing today?", "The service was nice and the car was fast"):
        assert best(say(tmp_path, text), wake.Jarvis()) < 0.1, text


def test_custom_phrase_from_three_takes(tmp_path):
    for f in ("melspectrogram.onnx", "embedding_model.onnx"):
        shutil.copy(wake.MODEL_DIR / f, tmp_path / f)
    maria = "Microsoft Maria Desktop"
    takes = [say(tmp_path, "Ola computador", maria, r) for r in (-3, 0, 3)]
    threshold = wake.enroll("ola computador", takes, tmp_path)
    score, thr = wake.detector({"wake": {"phrase": "ola computador"}}, tmp_path)
    assert thr == threshold
    assert best(say(tmp_path, "Ola computador", maria, -1), score, tmp_path) > thr
    for text in ("Olha o computador do vizinho quebrado", "Ola, tudo bem com voce?", "Abre o navegador por favor"):
        score, _ = wake.detector({"wake": {"phrase": "ola computador"}}, tmp_path)
        assert best(say(tmp_path, text, maria), score, tmp_path) < thr, text
    with pytest.raises(ValueError):  # a phrase that was never recorded
        wake.detector({"wake": {"phrase": "outra frase"}}, tmp_path)


def test_listener_fires_once_and_pauses_while_busy(tmp_path):
    audio, fired, quit_event = say(tmp_path, "Hey Jarvis"), [], threading.Event()

    def chunks(stop):
        for i in range(0, len(audio) - wake.CHUNK + 1, wake.CHUNK):
            if stop():
                return
            yield audio[i:i + wake.CHUNK]
        quit_event.set()

    def fire():
        fired.append(1)
        quit_event.set()

    wake.listen_forever({"wake": {"phrase": "hey jarvis"}}, fire, lambda: False, quit_event, chunks)
    assert fired == [1]
    quit_event.clear()
    busy = threading.Timer(0.3, quit_event.set)
    busy.start()
    wake.listen_forever({"wake": {"phrase": "hey jarvis"}}, fire, lambda: True, quit_event, chunks)
    assert fired == [1]  # a running session: the mic is not even opened
