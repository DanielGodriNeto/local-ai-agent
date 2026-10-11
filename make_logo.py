"""Rebuild assets/logo_<hex>.png (transparent, tinted) from assets/logo_src.jpg. Run after adding a state colour."""
from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).parent / "assets"
HEIGHT = 76
COLORS = ("82c341", "5d6b68", "ff6b6b")  # listening/done, idle, error: keep in sync with overlay.py


def alpha_from_jpg(path: Path) -> Image.Image:
    """The mark is darker than the near-white background: R channel -> alpha."""
    r = Image.open(path).convert("RGB").split()[0]
    bg, fg = 248, 130
    return r.point(lambda v: int(max(0, min(1, (bg - v) / (bg - fg))) * 255))


if __name__ == "__main__":
    alpha = alpha_from_jpg(ASSETS / "logo_src.jpg")
    alpha = alpha.crop(alpha.point(lambda v: 255 if v > 12 else 0).getbbox())
    alpha = alpha.resize((round(alpha.width * HEIGHT / alpha.height), HEIGHT), Image.LANCZOS)
    for hexcolor in COLORS:
        img = Image.new("RGBA", alpha.size, "#" + hexcolor)
        img.putalpha(alpha)
        img.save(ASSETS / f"logo_{hexcolor}.png")
    print(alpha.size, COLORS)
