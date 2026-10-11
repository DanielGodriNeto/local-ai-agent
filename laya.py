"""Laya (ONNX, choice classifier): input building, lazy scorer, adaptive shuffled voting."""
import math
import random
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

BOS, EOS, MASK = 2, 1, 4
MAX_LEN, MAX_OPTION_TOKENS = 511, 48
PROMPT = "choice question: the user gave a command. Which intent is it?"


def build_inputs(tokenizer, text: str, options: list[str]) -> dict:
    ids = [BOS, *tokenizer.encode(PROMPT).ids, EOS]
    pos = []
    for opt in options:
        pos.append(len(ids))
        ids += [MASK, *tokenizer.encode(opt).ids[:MAX_OPTION_TOKENS]]
    ids.append(EOS)
    ids += tokenizer.encode(text).ids[: max(0, MAX_LEN - len(ids))] + [EOS]
    return {"ids": ids, "pos": pos}


class LayaScorer:
    """Loads the ONNX model on first call. Per-process lifetime keeps RAM zero while idle."""

    def __init__(self, model_dir: str, threads: int = 4):
        self.dir, self.threads, self._sess, self._tok = Path(model_dir).expanduser(), threads, None, None

    def _load(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        self._sess = ort.InferenceSession(str(self.dir / "model_quantized.onnx"), opts,
                                          providers=["CPUExecutionProvider"])
        self._tok = Tokenizer.from_file(str(self.dir / "tokenizer.json"))

    def __call__(self, text: str, options: list[str]) -> list[float]:
        import numpy as np

        if self._sess is None:
            self._load()
        b = build_inputs(self._tok, text, options)
        n = len(b["ids"])
        logits = self._sess.run(None, {
            "input_ids": np.array([b["ids"]], dtype=np.int64),
            "attention_mask": np.ones((1, n), dtype=np.int64),
            "marker_pos": np.array([b["pos"]], dtype=np.int64),
            "marker_mask": np.ones((1, len(options)), dtype=bool),
            "qtype": np.array([0], dtype=np.int64),
        })[0][0]
        e = [math.exp(x - max(logits)) for x in logits]
        return [x / sum(e) for x in e]


def choose(score: Callable[[list[str]], list[float]], options: list[str], *, min_rounds=3,
           max_rounds=9, min_conf=0.6, rng: Optional[random.Random] = None) -> Optional[int]:
    """Vote over shuffled option orders (the model is deterministic, so order is the only
    variation). Stop once the leader is 2+ votes ahead; give up (None) without a strict
    majority and a mean probability >= min_conf."""
    rng = rng or random.Random()
    votes, probs, n = Counter(), [0.0] * len(options), 0
    while n < max_rounds:
        order = list(range(len(options)))
        rng.shuffle(order)
        p = score([options[i] for i in order])
        for k, i in enumerate(order):
            probs[i] += p[k]
        votes[order[max(range(len(p)), key=p.__getitem__)]] += 1
        n += 1
        top = votes.most_common(2)
        if n >= min_rounds and top[0][1] - (top[1][1] if len(top) > 1 else 0) >= 2:
            break
    best, count = votes.most_common(1)[0]
    return best if count > n / 2 and probs[best] / n >= min_conf else None
