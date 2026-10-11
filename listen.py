"""Voice session: one key press -> live text in the overlay -> silence ends it -> Laya decides -> action.

Bind this to a key (Hyprland: bind = SUPER, V, exec, python ~/.config/local-ai-agent/listen.py).
"""
import queue
import sys
import threading
import time
from collections import deque
from typing import Callable, Iterator, Optional

import numpy as np

import orchestrator as o
import overlay

RATE, BLOCK_S = 16000, 0.1


class Segmenter:
    """Energy VAD over 100 ms blocks. feed() -> 'start' | 'end' | 'timeout' | None."""

    def __init__(self, silence_s=1.0, no_speech_s=6.0, max_s=20.0, min_rms=0.012):
        self.silence_s, self.no_speech_s, self.max_s, self.min_rms = silence_s, no_speech_s, max_s, min_rms
        self.noise, self.speaking = min_rms / 3, False
        self.t = self.speech_t = self.silent_t = 0.0
        self.loud_run = 0

    def feed(self, block: np.ndarray) -> Optional[str]:
        rms = float(np.sqrt(np.mean(block ** 2)))
        loud = rms > max(self.min_rms, self.noise * 3)
        if not self.speaking:
            self.t += BLOCK_S
            self.loud_run = self.loud_run + 1 if loud else 0
            if self.loud_run >= 2:  # one click is not speech
                self.speaking, self.speech_t, self.silent_t = True, 0.0, 0.0
                return "start"
            if not loud:
                self.noise = 0.95 * self.noise + 0.05 * rms  # learn the room only from silence
            return "timeout" if self.t >= self.no_speech_s else None
        self.speech_t += BLOCK_S
        self.silent_t = 0.0 if loud else self.silent_t + BLOCK_S
        return "end" if self.silent_t >= self.silence_s or self.speech_t >= self.max_s else None


def listen(blocks: Iterator[np.ndarray], seg: Segmenter, transcribe: Callable[[np.ndarray], str],
           on_partial: Callable[[str], None], partial_every: float = 0.8) -> Optional[str]:
    """Consume blocks until the speaker stops; stream partial text meanwhile. None = nothing heard."""
    pre, buf, done = deque(maxlen=3), [], threading.Event()

    def partials():
        while not done.wait(partial_every):
            if seg.speaking and buf:
                on_partial(transcribe(np.concatenate(buf)))

    worker = threading.Thread(target=partials, daemon=True)
    worker.start()
    try:
        for block in blocks:
            event = seg.feed(block)
            if event == "start":
                buf.extend(pre)
            if seg.speaking:
                buf.append(block)
            else:
                pre.append(block)
            if event == "timeout":
                return None
            if event == "end":
                break
        else:
            return None  # source ended / cancelled
    finally:
        done.set()
        worker.join()
    return transcribe(np.concatenate(buf)).strip() or None


def mic_blocks(stop: threading.Event) -> Iterator[np.ndarray]:
    import sounddevice as sd

    q: queue.Queue = queue.Queue()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=int(RATE * BLOCK_S),
                        callback=lambda data, *_: q.put(data[:, 0].copy())):
        while not stop.is_set():
            try:
                yield q.get(timeout=0.2)
            except queue.Empty:
                pass


class LazyWhisper:
    """Loads in the background while the user starts talking (first run downloads the model)."""

    def __init__(self, model: str, language: str):
        self.language, self._lock, self._model = language, threading.Lock(), model
        threading.Thread(target=self._get, daemon=True).start()

    def _get(self):
        with self._lock:
            if isinstance(self._model, str):
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self._model, device="cpu", compute_type="int8")
            return self._model

    def __call__(self, audio: np.ndarray) -> str:
        segments, _ = self._get().transcribe(audio, language=self.language, beam_size=1,
                                              condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments)


def preload_laya(cfg: dict) -> Callable[[str, dict], dict]:
    """Load Laya while the user is still speaking, so the decision is instant at the end."""
    import laya

    lc = cfg["laya"]
    scorer = laya.LayaScorer(o.HERE / lc["model_dir"], lc["threads"])
    threading.Thread(target=scorer._load, daemon=True).start()
    return lambda text, c: o.laya_decide(text, c, scorer)


def session(ui: overlay.Overlay, cfg: dict, blocks: Optional[Iterator[np.ndarray]] = None) -> None:
    v = cfg["voice"]
    try:
        ui.status("OUVINDO", overlay.LISTEN)
        stt, decide = LazyWhisper(v["model"], v["language"]), preload_laya(cfg)
        seg = Segmenter(v["silence_s"], v["no_speech_timeout_s"], v["max_s"], v["min_rms"])
        stop = threading.Event()
        threading.Thread(target=lambda: (ui.closed.wait(), stop.set()), daemon=True).start()
        text = listen(blocks or mic_blocks(stop), seg, stt, ui.text, v["partial_every_s"])
        if not text:
            ui.status("NADA OUVIDO", overlay.IDLE)
            ui.text("Não ouvi nada.", overlay.IDLE)
            return ui.hold(1.5)
        ui.status("DECIDINDO", overlay.IDLE)
        ui.text(text)

        def confirm(question: str) -> bool:
            ok = ui.ask(question)
            if ok:
                ui.status("TRABALHANDO", overlay.IDLE)
            return ok

        out = o.handle(text, cfg, decide=decide, confirm=confirm)
        ui.status("PRONTO", overlay.GREEN)
        out = out.strip()[:600]
        ui.text(out or "Feito.")
        ui.hold(min(20.0, max(3.0, 2 + len(out) / 18)))
    except Exception as e:  # never leave the overlay stuck on screen
        ui.status("ERRO", overlay.RED)
        ui.text(f"{type(e).__name__}: {e}"[:300], overlay.RED)
        ui.hold(5)


def main() -> None:
    cfg = o.load_config()
    ui = overlay.Overlay(cfg["voice"].get("font", ""))
    threading.Thread(target=session, args=(ui, cfg), daemon=True).start()
    ui.run()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
