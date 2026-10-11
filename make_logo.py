"""Tinted, transparent logo PNGs built on demand from assets/logo_src.jpg (cached in assets/cache/)."""
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
CACHE = ASSETS / "cache"


def logo_png(hexcolor: str, height: int) -> Path | None:
    """Path to the logo tinted `hexcolor` at `height` px, or None if Pillow/the source is unavailable."""
    out = CACHE / f"logo_{hexcolor.lstrip('#')}_{height}.png"
    if out.exists():
        return out
    try:
        from PIL import Image

        r = Image.open(ASSETS / "logo_src.jpg").convert("RGB").split()[0]
        bg, fg = 248, 130  # near-white background vs the mark: red channel -> alpha
        alpha = r.point(lambda v: int(max(0, min(1, (bg - v) / (bg - fg))) * 255))
        alpha = alpha.crop(alpha.point(lambda v: 255 if v > 12 else 0).getbbox())
        alpha = alpha.resize((max(1, round(alpha.width * height / alpha.height)), height), Image.LANCZOS)
        img = Image.new("RGBA", alpha.size, "#" + hexcolor.lstrip("#"))
        img.putalpha(alpha)
        CACHE.mkdir(parents=True, exist_ok=True)
        img.save(out)
        return out
    except (ImportError, OSError, ValueError):
        return None
