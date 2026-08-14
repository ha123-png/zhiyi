"""Build a Windows multi-resolution icon with clean transparent corners."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_icon(source: Path, output: Path) -> None:
    image = Image.open(source).convert("RGBA")
    if image.size != (256, 256):
        image = image.resize((256, 256), Image.Resampling.LANCZOS)

    # The brand artwork is a rounded square. A supersampled mask keeps the
    # antialiased edge while making every pixel beyond it fully transparent;
    # this prevents Windows from compositing residual near-transparent white
    # corner pixels into a visible white box at desktop icon sizes.
    scale = 4
    mask = Image.new("L", (256 * scale, 256 * scale), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (2 * scale, 2 * scale, 253 * scale, 253 * scale),
        radius=31 * scale,
        fill=255,
    )
    mask = mask.resize((256, 256), Image.Resampling.LANCZOS)
    alpha = ImageChops.multiply(image.getchannel("A"), mask)
    image.putalpha(alpha)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="ICO", sizes=[(size, size) for size in ICON_SIZES])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build_icon(args.source, args.output)


if __name__ == "__main__":
    main()
