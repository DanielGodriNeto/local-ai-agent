"""Voice session: one key press -> live text in the overlay -> silence ends it -> intent matcher decides -> action.

Bind this to a key (Hyprland: bind = SUPER, V, exec, python ~/.config/local-ai-agent/listen.py).
"""
import queue
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np

import access
import earcons
import intent
import orchestrator as o
import overlay
import tts

RATE, BLOCK_S = 16000, 0.1
WHISPER_DIR = Path(__file__).parent / "models" / "whisper"
_TOKENIZER = {"tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
              "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"}
WHISPER = {  # size -> (pinned Systran/faster-whisper-<size> revision, sha256 per file). Weights: MIT.
    "base": ("ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", {
        "config.json": "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
        "model.bin": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9", **_TOKENIZER}),
    "small": ("536b0662742c02347bc0e980a01041f333bce120", {
        "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671", **_TOKENIZER}),
}
SHORT = 120  # answers up to this length fade after a few seconds; longer ones stay until Esc, click or hotkey
HISTORY: deque = deque(maxlen=10)  # (what was said, answer); RAM only, gone when the agent stops


class SileroVAD:
    """Speech probability per block, streamed through the Silero v6 model that ships with faster-whisper (~2 MB).
    Fans, music and keyboard clicks score low, unlike plain loudness."""
    CHUNK, CONTEXT = 512, 64

    def __init__(self):
        import os

        import faster_whisper.vad
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads, opts.inter_op_num_threads, opts.log_severity_level = 1, 1, 4
        path = os.path.join(os.path.dirname(faster_whisper.vad.__file__), "assets", "silero_vad_v6.onnx")
        self.sess = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        self.h = self.c = np.zeros((1, 1, 128), np.float32)
        self.ctx, self.rest = np.zeros(self.CONTEXT, np.float32), np.zeros(0, np.float32)

    def __call__(self, block: np.ndarray) -> float:
        audio, best = np.concatenate([self.rest, block.astype(np.float32)]), 0.0
        n = len(audio) // self.CHUNK * self.CHUNK
        for chunk in audio[:n].reshape(-1, self.CHUNK):
            out, self.h, self.c = self.sess.run(None, {"input": np.concatenate([self.ctx, chunk])[None],
                                                       "h": self.h, "c": self.c})
            self.ctx, best = chunk[-self.CONTEXT:], max(best, float(out.ravel()[0]))
        self.rest = audio[n:]
        return best


class Segmenter:
    """Speech segmentation over 100 ms blocks. feed() -> 'start' | 'end' | 'timeout' | None.
    With a vad (block -> speech probability) it uses that; without, plain loudness against the room noise."""

    def __init__(self, silence_s=1.0, no_speech_s=6.0, max_s=20.0, min_rms=0.012,
                 vad: Optional[Callable[[np.ndarray], float]] = None):
        self.silence_s, self.no_speech_s, self.max_s, self.min_rms = silence_s, no_speech_s, max_s, min_rms
        self.noise, self.speaking, self.vad = min_rms / 3, False, vad
        self.t = self.speech_t = self.silent_t = 0.0
        self.loud_run = 0

    def feed(self, block: np.ndarray) -> Optional[str]:
        rms = float(np.sqrt(np.mean(block ** 2)))
        if self.vad:  # hysteresis: harder to start (0.5) than to keep going (0.35)
            loud = self.vad(block) > (0.35 if self.speaking else 0.5)
        else:
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


def whisper_files(size: str) -> dict:
    """{file: (pinned url, sha256)} for intent.fetch_model."""
    rev, hashes = WHISPER[size]
    return {f: (f"{intent.HF}/Systran/faster-whisper-{size}/resolve/{rev}/{f}", sha) for f, sha in hashes.items()}


def whisper_ready(size: str) -> bool:
    return size in WHISPER and all((WHISPER_DIR / size / f).exists() for f in WHISPER[size][1])


def whisper_path(size: str) -> str:
    """A pinned size (downloaded and sha256-checked on first use) or a folder you already have; nothing else."""
    if size in WHISPER:
        intent.fetch_model(WHISPER_DIR / size, whisper_files(size))
        return str(WHISPER_DIR / size)
    if Path(size).expanduser().is_dir():
        return str(Path(size).expanduser())
    raise ValueError(f"modelo Whisper '{size}' sem versao fixa: use {' ou '.join(WHISPER)}, ou uma pasta local")


class LazyWhisper:
    """Loads in the background while the user starts talking (first run downloads the model)."""

    def __init__(self, model: str, language: str):
        self.language, self._lock, self._model = language, threading.Lock(), model
        threading.Thread(target=self._get, daemon=True).start()

    def _get(self):
        with self._lock:
            if isinstance(self._model, str):
                from faster_whisper import WhisperModel
                self._model = WhisperModel(whisper_path(self._model), device="cpu", compute_type="int8")
            return self._model

    def __call__(self, audio: np.ndarray) -> str:
        segments, _ = self._get().transcribe(audio, language=self.language, beam_size=1,
                                              condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments)


def preload_intent(cfg: dict) -> Callable[[str, dict], dict]:
    """Load the intent model while the user is still speaking, so the decision is instant at the end."""
    import intent

    ic = cfg["intent"]
    matcher = intent.Matcher(cfg["presets"], intent.E5(threads=ic["threads"]), min_score=ic["min_score"], margin=ic["margin"])
    threading.Thread(target=matcher.scores, args=("",), daemon=True).start()
    return lambda text, c: o.intent_decide(text, c, matcher)


def speaker(cfg: dict) -> Optional[tts.Speaker]:
    v = cfg["voice"]
    return tts.Speaker(v["language"], v.get("speed", 1.0)) if v.get("tts", True) and tts.available() else None


def session(ui: overlay.Overlay, cfg: dict, blocks: Optional[Iterator[np.ndarray]] = None,
            models: Optional[Callable[[], tuple]] = None, cancel: Optional[threading.Event] = None,
            typed: bool = False, detach: Optional[Callable[[], None]] = None,
            notify: Optional[Callable[[str, str], None]] = None) -> None:
    """models() -> (stt, decide[, speaker]) lets the daemon keep them loaded. Closing the overlay or cancel stops
    everything, including the voice. typed: a text box instead of the mic. detach(): called when a long task moves to
    the corner pill, so the daemon can start another session; notify(title, text) when it finishes there."""
    v, cancel = cfg["voice"], cancel or threading.Event()
    try:
        ui.status("Escreva" if typed else "Ouvindo", overlay.LISTEN)
        sound = v.get("earcons", True)
        if not typed:
            earcons.play("listen", sound)
        stt, decide, *extra = models() if models else (LazyWhisper(v["model"], v["language"]), preload_intent(cfg),
                                                        speaker(cfg))
        voice = extra[0] if extra else None
        def segmenter() -> Segmenter:  # one per utterance: its noise floor and the VAD state start fresh
            return Segmenter(v["silence_s"], v["no_speech_timeout_s"], v["max_s"], v["min_rms"],
                             vad=SileroVAD() if v.get("vad", "silero") == "silero" else None)
        stop = threading.Event()

        def watch():
            while not (ui.closed.wait(0.1) or cancel.is_set()):
                pass
            stop.set()
            cancel.set()  # also kills the AI and its child processes
            ui.close()
        threading.Thread(target=watch, daemon=True).start()
        if typed:
            text = ui.prompt("O que você precisa?")
            if not text:
                return ui.close()
        else:
            text = listen(blocks or mic_blocks(stop), segmenter(), stt, ui.text, v["partial_every_s"])
        if not text:
            ui.status("Nada ouvido", overlay.IDLE)
            earcons.play("error", sound)
            ui.text("Não ouvi nada.", overlay.IDLE)
            return ui.hold(1.5)
        ui.status("Pensando", overlay.IDLE)
        earcons.play("heard", sound)
        ui.text(text)

        def confirm(question: str) -> bool:
            ok = ui.ask(question)
            if ok:
                ui.status("Trabalhando", overlay.IDLE)
            return ok

        def hear(prompt: str) -> Optional[str]:  # one more answer, for the number grid
            if typed:
                return ui.prompt(prompt)
            ui.status(prompt, overlay.LISTEN)
            earcons.play("listen", sound)
            return listen(blocks or mic_blocks(stop), segmenter(), stt, ui.text, v["partial_every_s"])

        if (cmd := access.parse(text)):  # voice accessibility: no AI unless nothing matches, never in the background
            out = access.run({**cmd, "said": text}, cfg, access.desk(ui.gui, ui.win), hear, confirm, lambda q, opts: ui.choose(q, opts), cancel)
            ui.status("Pronto", overlay.GREEN)
            earcons.play("done", sound)
            ui.text(out)
            return ui.hold(3)
        streamed = []

        def on_event(e) -> None:  # the answer appears as the AI writes it; tool use shows in the tag
            if voice and not streamed:
                voice.warm()  # load the voice while the AI is still answering
            if e.kind == "tool":
                ui.status(e.text[:40], overlay.IDLE)
            else:
                streamed.append(e.text)
                ui.text("".join(streamed))

        working, moved = threading.Event(), threading.Event()

        def background() -> None:  # a long task moves to the corner pill so the PC (and the hotkey) is free again
            if not working.wait(cfg["ui"].get("background_after_s", 12)) and not cancel.is_set():
                moved.set()
                detach()
                ui.to_corner()
        if detach:
            threading.Thread(target=background, daemon=True).start()
        try:
            out = o.handle(text, cfg, decide=decide, confirm=confirm,
                           pick=lambda q, opts: ui.choose(q, opts), on_event=on_event, cancel=cancel).strip() or "Feito."
        finally:
            working.set()
        HISTORY.append((text, out))
        if moved.is_set():  # finished in the background: the pill says so, a click opens the answer
            ui.status("Pronto", overlay.GREEN)
            earcons.play("done", sound)
            ui.text(out)
            if notify and cfg["ui"].get("notify", True):
                notify("Tarefa pronta", out[:200])
            return ui.hold(None)
        ui.status("Pronto", overlay.GREEN)
        earcons.play("done", sound)
        ui.text(out)
        if voice and streamed:  # only the AI's answers are spoken, not preset results
            try:
                voice.say(out, cancel)
            except Exception:  # noqa: BLE001 - a broken voice must not replace a good answer with an error
                pass
            finally:
                voice.release()  # warm() loads it again while the AI writes the next answer
        ui.hold(4 if len(out) <= SHORT else None)
    except Exception as e:  # never leave the overlay stuck on screen
        ui.status("Erro", overlay.RED)
        earcons.play("error", cfg.get("voice", {}).get("earcons", True))
        ui.text(f"{type(e).__name__}: {e}"[:300], overlay.RED)
        ui.hold(5)


def main() -> None:
    cfg = o.load_config()
    ui = overlay.Overlay()
    threading.Thread(target=session, args=(ui, cfg), daemon=True).start()
    ui.run()


if __name__ == "__main__":
    if sys.stdout:  # pythonw (started by the hotkey) has no console
        sys.stdout.reconfigure(encoding="utf-8")
    main()
