#!/usr/bin/env python3
"""Regenerate the app icons (checked into git; rerun only if restyling).
Charcoal rounded tile with a gold play triangle — matches the Plextra theme.
Requires Pillow: pip install pillow
"""

from PIL import Image, ImageDraw

CHARCOAL = (25, 26, 28, 255)
RAISED = (35, 37, 39, 255)
GOLD = (229, 160, 13, 255)


def make(size: int, path: str) -> None:
    s = size * 4  # draw 4x then downsample for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = s // 6
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=CHARCOAL)
    d.rounded_rectangle(
        [s // 30, s // 30, s - s // 30, s - s // 30],
        radius=r, outline=RAISED, width=max(2, s // 40),
    )
    # play triangle, optically centered (nudged right)
    w = s * 0.42
    h = s * 0.48
    cx, cy = s * 0.54, s * 0.5
    d.polygon(
        [(cx - w / 2, cy - h / 2), (cx - w / 2, cy + h / 2), (cx + w / 2, cy)],
        fill=GOLD,
    )
    img.resize((size, size), Image.LANCZOS).save(path)
    print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    make(80, "icon.png")
    make(130, "largeIcon.png")
