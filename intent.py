"""Local intent matcher: multilingual-e5-small (int8 ONNX, ~118 MB) against example phrases per preset.

decide(text) -> run_preset | escalate (mode quick|task) | choose (two close presets) | clarify.
It never guesses: a low score, two close presets or an opposing verb ("fecha", "para") means it asks or escalates.
"""
import re
import threading
import unicodedata
from pathlib import Path
from typing import Callable, Optional

import numpy as np

MODEL_DIR = Path(__file__).parent / "models" / "e5"
HF = "https://huggingface.co"
MODEL_FILES = {  # file -> (pinned url, sha256); fetch_model() refuses anything else. Weights: MIT.
    "onnx/model_int8.onnx": (f"{HF}/Xenova/multilingual-e5-small/resolve/761b726dd34fb83930e26aab4e9ac3899aa1fa78/onnx/model_int8.onnx",
                             "4d24e2bc01a447951524466ef533e52944bf48509e6552810bcee1a2711cb02c"),
    # same vocabulary as tokenizer.json, but ~5 MB of RAM instead of ~250 MB with the `tokenizers` library
    "sentencepiece.bpe.model": (f"{HF}/intfloat/multilingual-e5-small/resolve/614241f622f53c4eeff9890bdc4f31cfecc418b3/sentencepiece.bpe.model",
                                "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865"),
}
BOS, PAD, EOS, UNK, MAX_TOKENS = 0, 1, 2, 3, 128  # XLM-R ids: sentencepiece id + 1, its <unk> (0) maps to 3
BUILTIN = {  # requests that go to the AI: quick = short answer, no tools; task = files/code, tools allowed
    "quick": ["qual a capital da França", "quanto é 15 vezes 7", "me conta uma piada", "que horas são no Japão",
              "o que significa essa palavra", "quem foi Santos Dumont", "what is the weather like", "traduz isso pro inglês"],
    "task": ["tem um bug no meu código", "refatora esse módulo", "explica o que esse repositório faz",
             "escreve um teste pra essa função", "procura nos meus arquivos", "corrige o erro 500 do login",
             "cria um script que renomeia arquivos", "fix the failing test"],
}
# All patterns run on plain() text: lower case, no accents ("pára" -> "para", "não" -> "nao").
FILLER = r"(?:(?:ei|hey|ok|jarvis|por favor|agora|entao)[\s,]*)*"
# "para" is also the preposition "for": count it only as the first word or after a modal verb
STOP_FIRST = re.compile(rf"^\s*{FILLER}(par[ae]r?|stop)\b")
STOP_MODAL = re.compile(r"\b(pode|quero|consegue|vai|preciso)\s+(par[ae]r?)\b")
STOP_ANY = re.compile(r"\b(fech\w*|deslig\w*|encerr\w*|cancel\w*|interromp\w*|termin(?:a|e|ar)\b|mat[ae]r?|"
                      r"sai(?:r|a)?|tir[ae]r?|remov\w*|close|quit|exit|kill|shut\w*)\b")
NEGATION = re.compile(r"\b(nao|nem|nunca|jamais|sem|deixa de|para de|pare de|don'?t|do not|never|no)\b")
QUESTION = re.compile(rf"^\s*{FILLER}(como|por ?que|o que|qual|quais|quanto|quando|onde|quem|sera|"
                      r"how|why|what|which|when|where|who)\b|\?\s*$")


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))


def question(text: str) -> bool:
    """A question about an action ("como eu abro o terminal?") is not the action."""
    return bool(QUESTION.search(plain(text)))


def opposing(text: str) -> bool:
    """True when the request asks to stop, close or NOT do something: it never runs an open/start preset."""
    t = plain(text)
    return any(p.search(t) for p in (STOP_ANY, STOP_FIRST, STOP_MODAL, NEGATION))


class E5:
    """Loads on first call; drop the instance to free the ~150 MB."""

    def __init__(self, model_dir: Path = MODEL_DIR, threads: int = 2):
        self.dir, self.threads, self._sess, self._tok = Path(model_dir), threads, None, None
        self._lock = threading.Lock()  # the preload thread and the first request may race

    def _load(self) -> None:
        import onnxruntime as ort
        import sentencepiece

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        # graph optimisation folds the int8 vocab table back to float32 (~400 MB instead of ~130 MB)
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        opts.enable_cpu_mem_arena = False
        self._sess = ort.InferenceSession(str(self.dir / "onnx" / "model_int8.onnx"), opts,
                                          providers=["CPUExecutionProvider"])
        self._tok = sentencepiece.SentencePieceProcessor(model_file=str(self.dir / "sentencepiece.bpe.model"))

    def _ids(self, text: str) -> list[int]:
        pieces = self._tok.encode(f"query: {text}")[:MAX_TOKENS - 2]
        return [BOS, *(i + 1 if i else UNK for i in pieces), EOS]

    def __call__(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            if self._sess is None:
                self._load()
        rows = [self._ids(t) for t in texts]
        width = max(map(len, rows))
        ids = np.array([r + [PAD] * (width - len(r)) for r in rows], dtype=np.int64)
        mask = (np.arange(width)[None, :] < np.array([len(r) for r in rows])[:, None]).astype(np.int64)
        hidden = self._sess.run(None, {"input_ids": ids, "attention_mask": mask,
                                       "token_type_ids": np.zeros_like(ids)})[0]
        pooled = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
        return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)


class Matcher:
    def __init__(self, presets: dict, embed: Optional[Callable[[list[str]], np.ndarray]] = None,
                 builtin: Optional[dict] = None, min_score: float = 0.86, margin: float = 0.02):
        self.embed, self.min_score, self.margin = embed or E5(), min_score, margin
        self.labels, phrases = [], []
        for name, p in presets.items():
            for ex in p.get("examples") or [p.get("description", name)]:
                self.labels.append(name)
                phrases.append(ex)
        for mode, examples in (builtin or BUILTIN).items():
            self.labels += [f"_{mode}"] * len(examples)
            phrases += examples
        self._phrases, self._vecs = phrases, None

    def scores(self, text: str) -> list[tuple[str, float]]:
        """Best example score per class, highest first."""
        if self._vecs is None:
            self._vecs = self.embed(self._phrases)
        sims = self._vecs @ self.embed([text])[0]
        best: dict[str, float] = {}
        for label, s in zip(self.labels, sims):
            best[label] = max(best.get(label, -1.0), float(s))
        return sorted(best.items(), key=lambda kv: -kv[1])

    def decide(self, text: str) -> dict:
        if not text.strip():
            return {"action": "clarify", "reply": "Não entendi, pode repetir?"}
        ranked = self.scores(text)
        (top, s1), (second, s2) = ranked[0], ranked[1]
        if top.startswith("_"):
            # a near-tie between task and quick on a question is a follow-up ("e qual a população dela?")
            near_quick = top == "_task" and second == "_quick" and s1 - s2 < self.margin and question(text)
            return {"action": "escalate", "mode": "quick" if near_quick else top[1:], "task": text}
        if question(text):
            return {"action": "escalate", "mode": "quick", "task": text}
        if opposing(text) or s1 < self.min_score:
            return {"action": "escalate", "mode": "task", "task": text}
        if s1 - s2 < self.margin:
            if second.startswith("_"):
                return {"action": "escalate", "mode": second[1:], "task": text}
            return {"action": "choose", "options": [top, second], "task": text}
        return {"action": "run_preset", "preset": top}


def fetch_model(model_dir: Path = MODEL_DIR) -> None:
    """Download the pinned model files and verify each sha256; a mismatch deletes the file and raises."""
    import hashlib
    import urllib.request

    for rel, (url, sha) in MODEL_FILES.items():
        dest = model_dir / rel
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, dest)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != sha:
            dest.unlink()
            raise RuntimeError(f"checksum mismatch for {rel}")


if __name__ == "__main__":
    fetch_model()
    print("modelo pronto em", MODEL_DIR)
