"""When Claude Code offers several distinct solutions it ends its answer with an ```options block; the user picks one."""
import json
import re
from typing import Optional

OPTIONS_RE = re.compile(r"```options\s*(\{.*?\})\s*```", re.S)


def parse_options(text: str) -> tuple[str, Optional[dict]]:
    """-> (text without the block, {"question", "options"} or None). Malformed blocks are dropped, never trusted."""
    m = OPTIONS_RE.search(text)
    if not m:
        return text, None
    clean = OPTIONS_RE.sub("", text).strip()
    try:
        d = json.loads(m.group(1))
        opts = [str(o).strip()[:200] for o in d["options"] if str(o).strip()]
        if not 2 <= len(opts) <= 9:
            return clean, None
        return clean, {"question": str(d.get("question", "Qual solução?"))[:200], "options": opts}
    except (ValueError, KeyError, TypeError):
        return clean, None


def terminal_pick(question: str, options: list[str]) -> Optional[int]:
    print(question)
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")
    try:
        n = int(input("Número (Enter cancela): ").strip())
    except (ValueError, EOFError):
        return None
    return n - 1 if 1 <= n <= len(options) else None
