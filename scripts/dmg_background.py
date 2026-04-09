#!/usr/bin/env python3
"""
Generate the DMG installer background for PhotoSort.

Usage: python scripts/dmg_background.py <output.png>

The window is 600×380. App icon sits at (160, 165), Applications link at (440, 165).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 600, 380
APP_X, APP_Y = 160, 165     # centre of app icon area
LNK_X, LNK_Y = 440, 165    # centre of Applications link area
ICON_R = 52                 # approximate icon radius at 100px icon-size


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _best_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return _font(path, size)
    return ImageFont.load_default()


def generate(out: str):
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # ── Background gradient (dark navy → slightly lighter) ──────────────────
    top    = (18, 18, 38)
    bottom = (30, 30, 58)
    for y in range(H):
        t = y / (H - 1)
        r = int(top[0] + t * (bottom[0] - top[0]))
        g = int(top[1] + t * (bottom[1] - top[1]))
        b = int(top[2] + t * (bottom[2] - top[2]))
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # ── Subtle separator line at top ─────────────────────────────────────────
    draw.line([(0, 58), (W, 58)], fill=(255, 255, 255, 20))

    # ── Title ────────────────────────────────────────────────────────────────
    f_title = _best_font(28)
    f_sub   = _best_font(12)

    title = "PhotoSort"
    bbox  = draw.textbbox((0, 0), title, font=f_title)
    tw    = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, 15), title, fill=(255, 255, 255), font=f_title)

    # ── Icon placeholder rings (subtle, just for visual balance) ─────────────
    accent = (80, 80, 160)
    for radius, alpha in [(ICON_R + 14, 30), (ICON_R + 4, 50)]:
        # Draw as concentric arcs via a sequence of points
        draw.ellipse(
            [APP_X - radius, APP_Y - radius, APP_X + radius, APP_Y + radius],
            outline=(*accent, alpha),
        )

    # ── Arrow ────────────────────────────────────────────────────────────────
    arrow_col = (120, 120, 200)
    x1 = APP_X + ICON_R + 18
    x2 = LNK_X - ICON_R - 18
    mid_y = (APP_Y + LNK_Y) // 2

    # Shaft
    shaft_h = 5
    draw.rounded_rectangle(
        [x1, mid_y - shaft_h // 2, x2 - 16, mid_y + shaft_h // 2],
        radius=3,
        fill=arrow_col,
    )
    # Head
    draw.polygon(
        [
            (x2 - 16, mid_y - 14),
            (x2,      mid_y),
            (x2 - 16, mid_y + 14),
        ],
        fill=arrow_col,
    )

    # ── Bottom hint text ─────────────────────────────────────────────────────
    hint = "Drag PhotoSort to the Applications folder to install"
    bbox = draw.textbbox((0, 0), hint, font=f_sub)
    sw   = bbox[2] - bbox[0]
    draw.text(((W - sw) // 2, H - 22), hint, fill=(150, 150, 180), font=f_sub)

    img.save(out)
    print(f"DMG background saved → {out}")


if __name__ == "__main__":
    generate(sys.argv[1] if len(sys.argv) > 1 else "dmg_background.png")
