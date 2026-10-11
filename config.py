"""Defaults in config.default.json, user overrides in config.json (git-ignored, written by settings.py)."""
import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT, USER = HERE / "config.default.json", HERE / "config.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(user: Path = USER, default: Path = DEFAULT) -> dict:
    cfg = json.loads(default.read_text(encoding="utf-8"))
    if user.exists():
        try:
            cfg = _merge(cfg, json.loads(user.read_text(encoding="utf-8")))
        except ValueError:
            pass  # a broken user file must never stop the agent: fall back to defaults
    return cfg


def save(cfg: dict, user: Path = USER) -> None:
    user.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
