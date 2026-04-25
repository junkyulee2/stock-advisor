"""Generate PWA icons for 춘규주식어플.
Run once: python webapp/static/icons/_generate.py

Produces:
  icon-192.png, icon-512.png, icon-maskable-512.png, apple-touch-icon.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
ROCKET = "🚀"


def make_gradient(size: int, c1=(87, 80, 241), c2=(139, 92, 246)) -> Image.Image:
    """Diagonal gradient from c1 (top-left) to c2 (bottom-right)."""
    img = Image.new("RGB", (size, size), c1)
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            px[x, y] = (r, g, b)
    return img


def round_corners(img: Image.Image, radius: int) -> Image.Image:
    rgba = img.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, rgba.size[0], rgba.size[1]), radius, fill=255)
    rgba.putalpha(mask)
    return rgba


def find_emoji_font() -> str | None:
    """Return path to an emoji-capable font (Windows Segoe UI Emoji or fallback)."""
    candidates = [
        r"C:\Windows\Fonts\seguiemj.ttf",
        r"C:\Windows\Fonts\seguisym.ttf",
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def draw_icon(size: int, *, padding_ratio: float = 0.18, corner_ratio: float = 0.22) -> Image.Image:
    """Standard icon: rounded square gradient + centered rocket emoji."""
    bg = make_gradient(size)
    bg = round_corners(bg, int(size * corner_ratio))
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    font_path = find_emoji_font()
    target_h = int(size * (1 - padding_ratio * 2))
    text = ROCKET
    if font_path:
        try:
            font = ImageFont.truetype(font_path, target_h)
        except OSError:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font, embedded_color=True)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    d.text((x, y), text, font=font, embedded_color=True)
    bg.alpha_composite(overlay)
    return bg


def draw_maskable(size: int) -> Image.Image:
    """Maskable icon: full bleed, content in safe area (~80%)."""
    bg = make_gradient(size)
    bg = bg.convert("RGBA")
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font_path = find_emoji_font()
    target_h = int(size * 0.55)
    text = ROCKET
    if font_path:
        try:
            font = ImageFont.truetype(font_path, target_h)
        except OSError:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font, embedded_color=True)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    d.text((x, y), text, font=font, embedded_color=True)
    bg.alpha_composite(overlay)
    return bg


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("generating icons in", OUT)
    for spec in [(192, "icon-192.png"), (512, "icon-512.png"), (180, "apple-touch-icon.png")]:
        size, name = spec
        draw_icon(size).save(OUT / name)
        print("wrote", name)
    draw_maskable(512).save(OUT / "icon-maskable-512.png")
    print("wrote icon-maskable-512.png")


if __name__ == "__main__":
    main()
