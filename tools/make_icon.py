"""Regenerate app.ico: a centred globe-and-keyboard glyph on a blue tile.

Each frame is drawn at its own size (8x supersampled) instead of being
scaled from one bitmap, so small title-bar and tray sizes stay sharp.

    python tools/make_icon.py [output.ico]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 20, 24, 32, 40, 48, 64, 256)
SCALE = 8
BLUE = (26, 115, 232, 255)
WHITE = (255, 255, 255, 255)
GREEN = (52, 168, 83, 255)


def _frame(size: int) -> Image.Image:
    s = size * SCALE
    unit = s / 256
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(48 * unit), fill=BLUE)

    def snap(value: float) -> int:
        # Align edges to whole target pixels so small frames stay crisp.
        return round(value / SCALE) * SCALE

    compact = size <= 24
    globe = 132 if compact else 112
    gap = 20 if compact else 16
    key_h = 0 if compact else 15
    key_gap = 0 if compact else 11
    bar_h = 22 if compact else 15
    total = globe + gap + key_h + key_gap + bar_h
    top = (256 - total) / 2

    # Globe: solid white disc with blue meridian, equator and latitude lines.
    g = snap(globe * unit)
    gx = snap((s - g) / 2)
    gy = snap(top * unit)
    draw.ellipse((gx, gy, gx + g - 1, gy + g - 1), fill=WHITE)
    line = max(SCALE, round((10 if compact else 6) * unit))
    cx, cy = gx + g / 2, gy + g / 2
    draw.line((gx, cy, gx + g, cy), fill=BLUE, width=line)
    meridian = g * 0.42
    draw.ellipse((cx - meridian / 2, gy, cx + meridian / 2, gy + g - 1), outline=BLUE, width=line)
    if not compact:
        for offset in (-0.28, 0.28):
            y = cy + g * offset
            half = (g / 2) * (1 - (2 * offset) ** 2) ** 0.5
            draw.line((cx - half, y, cx + half, y), fill=BLUE, width=line)

    y = gy + g + snap(gap * unit)
    radius = max(SCALE, round(4 * unit))
    if not compact:
        # Three keycaps; the green one marks the CapsLock key.
        key_w, key_space = 26, 8
        row = 3 * key_w + 2 * key_space
        x = (s - row * unit) / 2
        for colour in (WHITE, GREEN, WHITE):
            draw.rounded_rectangle(
                (snap(x), y, snap(x + key_w * unit) - 1, y + snap(key_h * unit) - 1),
                radius=radius, fill=colour,
            )
            x += (key_w + key_space) * unit
        y += snap((key_h + key_gap) * unit)

    # Space bar.
    bar_w = snap((96 if compact else 46) * unit)
    bx = snap((s - bar_w) / 2)
    draw.rounded_rectangle(
        (bx, y, bx + bar_w - 1, y + snap(bar_h * unit) - 1), radius=radius, fill=WHITE,
    )
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "app.ico"
    frames = [_frame(size) for size in SIZES]
    largest = frames[-1]
    largest.save(output, format="ICO", sizes=[(f.width, f.height) for f in frames], append_images=frames[:-1])
    print(f"Wrote {output} with sizes {', '.join(str(size) for size in SIZES)}")


if __name__ == "__main__":
    main()
