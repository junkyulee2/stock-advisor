"""Generate a high-res multi-size .ico for the Stock Advisor desktop shortcut.

Design: dark navy gradient background, stylized rising candlestick chart,
subtle up-arrow accent. Saved as assets/icon.ico with sizes 16-256.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "icon.ico"

BASE = 1024  # design at 4x res, downscale

# palette
BG_TOP = (15, 23, 42)       # deep navy
BG_BOT = (2, 6, 23)          # near black
GREEN = (34, 197, 94)        # emerald
GREEN_DARK = (21, 128, 61)
RED = (239, 68, 68)
WHITE = (240, 245, 255)
GOLD = (251, 191, 36)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_base(size: int = BASE) -> Image.Image:
    # Vertical gradient background on a rounded-square canvas.
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # gradient
    grad = Image.new("RGB", (1, size))
    gdraw = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / size
        gdraw.point((0, y), fill=lerp(BG_TOP, BG_BOT, t))
    grad = grad.resize((size, size))

    # rounded mask
    mask = Image.new("L", (size, size), 0)
    mdraw = ImageDraw.Draw(mask)
    r = int(size * 0.22)
    mdraw.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    img.paste(grad, (0, 0), mask)

    # subtle inner glow border
    border = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(border)
    bdraw.rounded_rectangle(
        (6, 6, size - 7, size - 7), radius=r - 6, outline=(80, 120, 180, 130), width=6
    )
    border = border.filter(ImageFilter.GaussianBlur(2))
    img.alpha_composite(border)

    return img


def draw_candle(draw, x, y_open, y_close, y_high, y_low, width, color_up, color_down):
    color = color_up if y_close < y_open else color_down
    # wick
    draw.rectangle(
        (x - width * 0.09, y_high, x + width * 0.09, y_low),
        fill=color,
    )
    # body
    top = min(y_open, y_close)
    bot = max(y_open, y_close)
    draw.rectangle((x - width / 2, top, x + width / 2, bot), fill=color)


def draw_chart(img: Image.Image):
    size = img.size[0]
    draw = ImageDraw.Draw(img)

    # chart area
    left = int(size * 0.16)
    right = int(size * 0.84)
    top = int(size * 0.28)
    bot = int(size * 0.78)

    # baseline grid (subtle)
    for i in range(1, 4):
        y = top + (bot - top) * i / 4
        draw.line((left, y, right, y), fill=(255, 255, 255, 25), width=2)

    # candlesticks — rising trend
    n = 7
    w = (right - left) / n
    base_prices = [0.72, 0.65, 0.58, 0.50, 0.40, 0.28, 0.15]  # fraction of chart height from top
    for i, bp in enumerate(base_prices):
        x = left + w * (i + 0.5)
        if i == 0:
            y_open = top + (bot - top) * 0.78
        else:
            y_open = top + (bot - top) * base_prices[i - 1]
        y_close = top + (bot - top) * bp
        y_high = y_close - w * 0.35
        y_low = y_open + w * 0.25
        draw_candle(draw, x, y_open, y_close, y_high, y_low, w * 0.58, GREEN, RED)

    # overlay smooth rising line (trend)
    pts = []
    for i, bp in enumerate(base_prices):
        pts.append((left + w * (i + 0.5), top + (bot - top) * bp))
    for j in range(len(pts) - 1):
        draw.line((pts[j], pts[j + 1]), fill=GREEN, width=int(size * 0.012))

    # endpoint dot
    ex, ey = pts[-1]
    r = int(size * 0.022)
    draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=WHITE, outline=GREEN, width=int(size * 0.008))

    # top-left upward arrow glyph
    ax, ay = int(size * 0.19), int(size * 0.19)
    al = int(size * 0.09)
    draw.polygon(
        [(ax, ay + al), (ax + al / 2, ay), (ax + al, ay + al)],
        fill=GOLD,
    )
    draw.rectangle(
        (ax + al * 0.38, ay + al * 0.55, ax + al * 0.62, ay + al * 1.05),
        fill=GOLD,
    )


def main():
    big = make_base(BASE)
    draw_chart(big)

    # glow pass
    glow = big.filter(ImageFilter.GaussianBlur(6))
    final = Image.alpha_composite(glow, big)

    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 192, 256]
    frames = [final.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(OUT_PATH, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[1:])

    # also save a PNG preview
    preview = OUT_DIR / "icon_preview.png"
    final.resize((512, 512), Image.LANCZOS).save(preview, "PNG")
    print(f"saved: {OUT_PATH}")
    print(f"preview: {preview}")


if __name__ == "__main__":
    main()
