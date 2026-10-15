"""Wake word, fully local and light (~3.5 MB of models, a few % of one core): say it and a session starts.

    "hey jarvis"   openWakeWord's pretrained model (weights CC BY-NC-SA 4.0: personal use; downloaded, not shipped)
    your phrase    `python wake.py --record "ola computador"`: say it 3 times; it is matched against those recordings

`python wake.py` downloads the pinned models. Audio never leaves the PC and is never stored, only the recordings you
make for your own phrase (models/wake/custom.npz, as speech features, not audio).
"""
import argparse
from collections import deque
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np

MODEL_DIR = Path(__file__).parent / "models" / "wake"
REL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
MODEL_FILES = {
    "melspectrogram.onnx": (f"{REL}/melspectrogram.onnx", "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f"),
    "embedding_model.onnx": (f"{REL}/embedding_model.onnx", "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f"),
    "hey_jarvis_v0.1.onnx": (f"{REL}/hey_jarvis_v0.1.onnx", "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb"),
}
RATE, CHUNK = 16000, 1280  # 80 ms: one speech embedding per chunk
CUSTOM = MODEL_DIR / "custom.npz"
LOG = Path.home() / ".local-ai-agent" / "agentd.log"  # the daemon runs without a console


def session(path: Path):
    import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads, opts.inter_op_num_threads, opts.log_severity_level = 1, 1, 4
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


class Features:
    """Streaming openWakeWord front end: 80 ms of 16 kHz audio -> one 96-d speech embedding (Google, Apache-2.0)."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        self.mel, self.emb = session(model_dir / "melspectrogram.onnx"), session(model_dir / "embedding_model.onnx")
        self.tail = np.zeros(480, np.float32)  # 3 hops of overlap so frames line up across chunks
        self.mels = np.ones((76, 32), np.float32)

    def __call__(self, chunk: np.ndarray) -> np.ndarray:
        x = np.concatenate([self.tail, chunk.astype(np.float32) * 32767])  # the model expects int16-scaled samples
        self.tail = x[-480:]
        spec = self.mel.run(None, {"input": x[None]})[0].reshape(-1, 32) / 10 + 2
        self.mels = np.vstack([self.mels, spec])[-76:]
        return self.emb.run(None, {"input_1": self.mels[None, :, :, None]})[0].reshape(96)


class Jarvis:
    """Pretrained "hey jarvis": a score per embedding from the last 16 (1.3 s)."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        self.sess, self.embs = session(model_dir / "hey_jarvis_v0.1.onnx"), deque(maxlen=16)

    def __call__(self, emb: np.ndarray) -> float:
        self.embs.append(emb)
        if len(self.embs) < 16:
            return 0.0
        return float(self.sess.run(None, {"x.1": np.array(self.embs, np.float32)[None]})[0].ravel()[0])


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """1 - mean cosine distance along the best time alignment (DTW), so a slower or faster take still matches."""
    an, bn = a / np.linalg.norm(a, axis=1, keepdims=True), b / np.linalg.norm(b, axis=1, keepdims=True)
    cost = 1 - an @ bn.T
    acc = np.full((len(a) + 1, len(b) + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    return float(1 - acc[-1, -1] / (len(a) + len(b)))



class Template:
    """Your own phrase: the last embeddings compared with each recording (centred on their mean, which removes what
    all speech has in common); the best match is the score."""

    def __init__(self, templates: list, mean: np.ndarray):
        self.mean, self.templates = mean, [t - mean for t in templates]
        self.embs = deque(maxlen=max(len(t) for t in templates))

    def __call__(self, emb: np.ndarray) -> float:
        self.embs.append(emb - self.mean)
        recent = np.array(self.embs)
        return max((similarity(t, recent[-len(t):]) for t in self.templates if len(recent) >= len(t) // 2),
                   default=0.0)


def embeddings(audio: np.ndarray, model_dir: Path = MODEL_DIR) -> np.ndarray:
    feats = Features(model_dir)
    return np.array([feats(audio[i:i + CHUNK]) for i in range(0, len(audio) - CHUNK + 1, CHUNK)])


def phrase_part(audio: np.ndarray, vad: Callable[[np.ndarray], float], model_dir: Path = MODEL_DIR) -> np.ndarray:
    """Embeddings of the spoken part of a take (each embedding looks 0.76 s back, so keep ~10 chunks after it ends)."""
    speech = [i for i in range(0, len(audio) - CHUNK + 1, CHUNK) if vad(audio[i:i + CHUNK]) > 0.5]
    if not speech:
        raise ValueError("nao ouvi nada nessa gravacao")
    return embeddings(audio, model_dir)[speech[0] // CHUNK:speech[-1] // CHUNK + 10]


def enroll(phrase: str, takes: list, model_dir: Path = MODEL_DIR, vad_factory: Optional[Callable] = None) -> float:
    """Save your phrase from 3+ takes. Threshold: how alike your own takes are, minus a safety margin."""
    import listen
    templates = [phrase_part(t, (vad_factory or listen.SileroVAD)(), model_dir) for t in takes]
    mean = np.concatenate(templates).mean(0)
    centred = [t - mean for t in templates]
    cross = [similarity(a, b) for i, a in enumerate(centred) for b in centred[i + 1:]]
    threshold = max(0.75, min(cross) - 0.05)
    np.savez(model_dir / "custom.npz", phrase=phrase, mean=mean, threshold=threshold,
             **{f"t{i}": t for i, t in enumerate(templates)})
    return threshold


def detector(cfg: dict, model_dir: Path = MODEL_DIR) -> tuple[Callable[[np.ndarray], float], float]:
    """(embedding -> score, threshold) for the configured phrase."""
    w = cfg["wake"]
    if w["phrase"].strip().lower() == "hey jarvis":
        return Jarvis(model_dir), w.get("threshold", 0.5)
    if not (model_dir / "custom.npz").exists():
        raise ValueError(f'grave a frase primeiro: python wake.py --record "{w["phrase"]}"')
    data = np.load(model_dir / "custom.npz")
    if str(data["phrase"]) != w["phrase"]:
        raise ValueError(f'grave a frase primeiro: python wake.py --record "{w["phrase"]}"')
    templates = [data[k] for k in data.files if k.startswith("t") and k[1:].isdigit()]
    return Template(templates, data["mean"]), float(data["threshold"])


def ready(cfg: dict, model_dir: Path = MODEL_DIR) -> bool:
    w = cfg.get("wake", {})
    if not w.get("enabled") or not all((model_dir / f).exists() for f in MODEL_FILES):
        return False
    return w["phrase"].strip().lower() == "hey jarvis" or (model_dir / "custom.npz").exists()


def mic_chunks(stop) -> Iterator[np.ndarray]:
    import queue

    import sounddevice as sd
    q: queue.Queue = queue.Queue()
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=CHUNK,
                        callback=lambda data, *_: q.put(data[:, 0].copy())):
        while not stop():
            try:
                yield q.get(timeout=0.2)
            except queue.Empty:
                pass


def listen_forever(cfg: dict, fire: Callable[[], None], busy: Callable[[], bool], quit_event,
                   chunks: Callable = mic_chunks, model_dir: Path = MODEL_DIR) -> None:
    """Runs in the daemon. The mic is closed while a session runs (it has its own), so the agent never wakes itself."""
    import contextlib
    import time
    import traceback
    while not quit_event.is_set():
        if busy():
            time.sleep(0.2)
            continue
        heard = False
        try:
            feats, (score, threshold) = Features(model_dir), detector(cfg, model_dir)
            with contextlib.closing(chunks(lambda: busy() or quit_event.is_set())) as stream:
                for chunk in stream:
                    if score(feats(chunk)) >= threshold:
                        heard = True
                        break
        except Exception as e:  # no mic, a broken recording...: log it and retry, never kill the daemon silently
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as log:
                log.write(f"wake: {e}\n")
                traceback.print_exc(file=log)
            if isinstance(e, ValueError):  # the phrase in the config was never recorded: retrying will not help
                return
            quit_event.wait(5)
            continue
        if heard:  # the mic is closed by now, so the session can open its own
            fire()
            time.sleep(0.5)  # let the session start before checking busy() again


def record_takes(n: int = 3, seconds: float = 2.5) -> list:
    import sounddevice as sd

    import earcons
    takes = []
    for i in range(n):
        input(f"[{i + 1}/{n}] Enter, e diga a frase depois do bip...")
        earcons.play("listen")
        sd.sleep(250)
        takes.append(sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="float32", blocking=True)[:, 0])
        earcons.play("heard")
    return takes


def main() -> None:
    import intent
    p = argparse.ArgumentParser(description="Baixa os modelos; --record grava a sua frase.")
    p.add_argument("--record", metavar="FRASE")
    a = p.parse_args()
    intent.fetch_model(MODEL_DIR, MODEL_FILES)
    if not a.record:
        print('pronto: "hey jarvis" ligado. Frase propria: python wake.py --record "sua frase"')
        return
    threshold = enroll(a.record, record_takes())
    print(f'frase salva (limiar {threshold:.2f}). Ponha "wake": {{"phrase": "{a.record}"}} no config.json')


if __name__ == "__main__":
    main()
