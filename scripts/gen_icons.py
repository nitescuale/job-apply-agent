"""Generate Chrome extension icons for Job Apply Agent.

Design: rounded-square ink badge (#23241f) with white 'J' (Hanken Grotesk Bold
if available, else Arial Bold). The 128px variant adds a small green accent
dot (#3d7d5a) bottom-right for brand consistency with the popup.

Outputs three opaque-on-transparent PNGs that fully fill their NxN canvas:
  extension/public/icons/icon16.png
  extension/public/icons/icon48.png
  extension/public/icons/icon128.png
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

INK = (35, 36, 31, 255)        # --ink #23241f
ACCENT = (61, 125, 90, 255)    # --ac  #3d7d5a
WHITE = (247, 247, 245, 255)   # --bg  #f7f7f5 (soft white, brand-consistent)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "extension" / "public" / "icons"


def find_font(size: int) -> ImageFont.FreeTypeFont:
    """Try Hanken Grotesk Bold, fall back to Arial Bold, then default."""
    candidates = [
        r"C:\Windows\Fonts\HankenGrotesk-Bold.ttf",
        r"C:\Windows\Fonts\HankenGrotesk-ExtraBold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_icon(size: int) -> Image.Image:
    """Render a single NxN icon: rounded ink square + white J + (opt) accent dot."""
    # 4x supersample for crisp edges at small sizes
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded square fills the canvas with a tiny inset so corners don't clip
    inset = max(1, s // 64)
    radius = int(s * 0.22)  # ~22% corner radius — Chrome-friendly app icon
    draw.rounded_rectangle(
        [(inset, inset), (s - inset - 1, s - inset - 1)],
        radius=radius,
        fill=INK,
    )

    # Letter J — sized so it dominates at 16px
    # font size ~= 78% of canvas; works for J's tall narrow form
    font_size = int(s * 0.78)
    font = find_font(font_size)

    text = "J"
    # Anchor middle-middle for clean optical centering
    cx, cy = s // 2, int(s * 0.50)
    # Slight upward nudge — J's descender visually drags it down
    cy -= int(s * 0.02)
    draw.text((cx, cy), text, font=font, fill=WHITE, anchor="mm")

    # Accent dot at 48/128 only — too noisy at 16
    if size >= 48:
        dot_r = int(s * 0.07)
        margin = int(s * 0.13)
        cx_d = s - margin
        cy_d = s - margin
        draw.ellipse(
            [(cx_d - dot_r, cy_d - dot_r), (cx_d + dot_r, cy_d + dot_r)],
            fill=ACCENT,
        )

    # Downsample with LANCZOS for crisp result
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in (16, 48, 128):
        icon = render_icon(size)
        out_path = OUT_DIR / f"icon{size}.png"
        # optimize=True keeps file size minimal
        icon.save(out_path, format="PNG", optimize=True)
        w, h = icon.size
        kb = out_path.stat().st_size / 1024
        print(f"{out_path.name}: {w}x{h}, {kb:.2f} KB")


if __name__ == "__main__":
    main()
