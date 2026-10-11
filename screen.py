"""Read a monitor on request ("o que tem no monitor 2"): local OCR text, or the image when explicitly asked.

Nothing is captured unless the spoken request asks for it, and nothing is saved except a temp PNG for image mode.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

OCR_MAX_CHARS = 6000
ORDINALS = {"primeiro": 1, "segundo": 2, "terceiro": 3, "quarto": 4, "um": 1, "dois": 2, "tres": 3, "três": 3, "quatro": 4,
            "principal": 1, "secundario": 2, "secundário": 2}
SCREEN = re.compile(r"\b(tela|monitor|screen)\b", re.I)
VERB = re.compile(r"\b(l[eêi]\w*|resum\w+|olh\w+|v[eêi]\w*|analis\w+|descrev\w+|o que (tem|est[aá]|aparece)|que que (tem|est[aá]))", re.I)
VISUAL = re.compile(r"\b(olh\w+|v[eêi]a?|imagem|print|screenshot|visualmente|foto)\b", re.I)


def parse_request(text: str) -> Optional[dict]:
    """-> {"monitor": int, "image": bool} when the user asks to read a screen, else None."""
    if not (SCREEN.search(text) and VERB.search(text)):
        return None
    words, monitor = re.findall(r"\w+", text.lower()), 1
    for i, w in enumerate(words):
        if w == "monitor":  # "monitor 2", "monitor secundario", "segundo monitor"
            for near in words[i + 1:i + 2] + words[max(0, i - 1):i]:
                if near.isdigit() or near in ORDINALS:
                    monitor = int(near) if near.isdigit() else ORDINALS[near]
                    break
    return {"monitor": monitor, "image": bool(VISUAL.search(text))}


def capture(mon: dict):
    """PIL image of one monitor."""
    if sys.platform == "win32":
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=(mon["x"], mon["y"], mon["x"] + mon["w"], mon["y"] + mon["h"]), all_screens=True)
    import io
    from PIL import Image
    png = subprocess.run(["grim", "-g", f"{mon['x']},{mon['y']} {mon['w']}x{mon['h']}", "-"],
                         capture_output=True, timeout=10, check=True).stdout
    return Image.open(io.BytesIO(png))


def ocr_text(img) -> str:
    from rapidocr import RapidOCR  # heavy import, only when the user asks for it
    import numpy as np
    out = RapidOCR()(np.array(img.convert("RGB")))
    return "\n".join(out.txts or ())[:OCR_MAX_CHARS]


def save_temp(img) -> Path:
    """PNG in a private temp dir (mkdtemp is 0700); the caller deletes it with shutil.rmtree(path.parent)."""
    path = Path(tempfile.mkdtemp(prefix="screen_")) / "screen.png"
    img.save(path)
    return path


def cleanup(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)
