"""Spoken answers with Kokoro (weights Apache-2.0; ~430 MB RAM while loaded, ~0.2 s per second of speech on CPU).

Optional: `pip install kokoro-onnx` (its phonemizer/espeak-ng are GPL-3; installed by you, not shipped here), then
`python tts.py` downloads the pinned model (~340 MB). Without them the agent just stays silent.
"""
import re
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

MODEL_DIR = Path(__file__).parent / "models" / "kokoro"
REL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
MODEL_FILES = {  # fp32: int8 measured 12x slower on CPU, fp16 no lighter
    "kokoro-v1.0.onnx": (f"{REL}/kokoro-v1.0.onnx", "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
    "voices-v1.0.bin": (f"{REL}/voices-v1.0.bin", "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
VOICES = {  # voice.language -> (voice, espeak language)
    "pt": ("pf_dora", "pt-br"), "en": ("af_heart", "en-us"), "es": ("ef_dora", "es"), "fr": ("ff_siwis", "fr-fr"),
    "it": ("if_sara", "it"), "hi": ("hf_alpha", "hi"), "ja": ("jf_alpha", "ja"), "zh": ("zf_xiaobei", "cmn"),
}
SPEAK_MAX = 400  # longer answers: read the start, the rest stays on screen


def speakable(text: str) -> list[str]:
    """Plain sentences: no code blocks or markdown symbols; long answers cut at a sentence boundary."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_#`>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    sentences, out, size = re.split(r"(?<=[.!?…])\s+", text), [], 0
    for s in sentences:
        if out and size + len(s) > SPEAK_MAX:
            break
        out.append(s[:SPEAK_MAX])
        size += len(s)
    return [s for s in out if s]


def available(model_dir: Path = MODEL_DIR) -> bool:
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return all((model_dir / name).exists() for name in MODEL_FILES)


class Speaker:
    """Loads Kokoro on warm() or the first say(); synthesizes the next sentence while the current one plays."""

    def __init__(self, language: str = "pt", speed: float = 1.0, model_dir: Path = MODEL_DIR,
                 synth: Optional[Callable[[str], tuple]] = None, play: Optional[Callable] = None):
        self.voice, self.lang = VOICES.get(language, VOICES["en"])
        self.speed, self.dir, self._synth, self._play = speed, model_dir, synth, play
        self._lock, self._k = threading.Lock(), None

    def warm(self) -> None:
        threading.Thread(target=self._model, daemon=True).start()

    def _model(self):
        with self._lock:
            if self._k is None:
                from kokoro_onnx import Kokoro
                self._k = Kokoro(str(self.dir / "kokoro-v1.0.onnx"), str(self.dir / "voices-v1.0.bin"))
            return self._k

    def release(self) -> None:
        """Kokoro is the heaviest model (~350 MB) and only speaks at the end: drop it until the next warm()."""
        with self._lock:
            self._k = None

    def synth(self, sentence: str) -> tuple:
        if self._synth:
            return self._synth(sentence)
        return self._model().create(sentence, voice=self.voice, speed=self.speed, lang=self.lang)

    def say(self, text: str, cancel: threading.Event) -> None:
        import sounddevice as sd
        play, wait, done = self._play or sd.play, (lambda: None) if self._play else sd.wait, threading.Event()

        def watch():  # cancel cuts the voice mid-word
            while not done.wait(0.05):
                if cancel.is_set():
                    sd.stop()
                    return
        if not self._play:
            threading.Thread(target=watch, daemon=True).start()
        try:
            for sentence in speakable(text):
                if cancel.is_set():
                    return
                audio, rate = self.synth(sentence)  # runs while the previous sentence is still playing
                wait()
                if cancel.is_set():
                    return
                play(np.asarray(audio, np.float32), rate)
            wait()
        finally:
            done.set()


if __name__ == "__main__":
    import intent
    intent.fetch_model(MODEL_DIR, MODEL_FILES)
    print("voz pronta em", MODEL_DIR)
