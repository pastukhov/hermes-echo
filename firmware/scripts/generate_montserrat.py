"""Build compact 4-bit Montserrat glyph assets for the StickS3 screen.

Input: Google Fonts Montserrat[wght].ttf (SHA-256 below). Generated bitmap
data remains under the SIL Open Font License in firmware/fonts/OFL-Montserrat.txt.
Requires Pillow. Run: python firmware/scripts/generate_montserrat.py FONT.ttf OUT.c
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PIL import ImageFont


SOURCE_SHA256 = "0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7"
SIZES = [("small", 10, 500), ("hint", 11, 500), ("title", 20, 600)]
CODEPOINTS = list(range(32, 127)) + [0x0401] + list(range(0x0410, 0x0450)) + [0x0451]


def c_array(name: str, values: list[int], per_line: int = 16) -> str:
    lines = [f"static const uint8_t {name}[] = {{"]
    for i in range(0, len(values), per_line):
        lines.append("  " + ", ".join(f"0x{v:02x}" for v in values[i:i + per_line]) + ",")
    return "\n".join(lines + ["};"])


def generate(font_path: Path) -> str:
    digest = hashlib.sha256(font_path.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"unexpected Montserrat source SHA-256: {digest}")
    chunks = [
        "/* Generated from Google Fonts Montserrat[wght].ttf, SHA-256 " + digest + ".",
        " * Font data is licensed under SIL OFL 1.1: firmware/fonts/OFL-Montserrat.txt. */",
        '#include "screen_font_data.h"',
    ]
    for name, pixels, weight in SIZES:
        font = ImageFont.truetype(str(font_path), pixels)
        font.set_variation_by_axes([weight])
        bitmap: list[int] = []
        glyphs: list[tuple[int, int, int, int, int, int, int]] = []
        for codepoint in CODEPOINTS:
            character = chr(codepoint)
            mask, (left, top) = font.getmask2(character, mode="L")
            width, height = mask.size
            advance = round(font.getlength(character))
            if not (0 <= width <= 255 and 0 <= height <= 255 and
                    -128 <= left <= 127 and -128 <= top <= 127 and
                    0 <= advance <= 255):
                raise ValueError(f"glyph metrics out of range: U+{codepoint:04X}")
            offset = len(bitmap)
            coverage = [round(value * 15 / 255) for value in bytes(mask)]
            for i in range(0, len(coverage), 2):
                bitmap.append((coverage[i] << 4) |
                              (coverage[i + 1] if i + 1 < len(coverage) else 0))
            glyphs.append((codepoint, width, height, left, top, advance, offset))
        chunks.append(c_array(f"montserrat_{name}_bitmap", bitmap))
        chunks.append(f"static const screen_font_glyph_t montserrat_{name}_glyphs[] = {{")
        for codepoint, width, height, left, top, advance, offset in glyphs:
            chunks.append(
                f"  {{0x{codepoint:04x}, {width}, {height}, {left}, {top}, {advance}, {offset}}},"
            )
        chunks.append("};")
        chunks.append(
            f"static const screen_font_data_t montserrat_{name} = "
            f"{{montserrat_{name}_glyphs, montserrat_{name}_bitmap, {len(glyphs)}}};"
        )
    chunks.extend([
        "const screen_font_data_t *screen_font_data_get(screen_font_size_t size) {",
        "  switch (size) {",
        "    case SCREEN_FONT_SMALL: return &montserrat_small;",
        "    case SCREEN_FONT_HINT: return &montserrat_hint;",
        "    case SCREEN_FONT_TITLE: return &montserrat_title;",
        "    default: return 0;",
        "  }",
        "}",
    ])
    return "\n".join(chunks) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate_montserrat.py FONT.ttf OUTPUT.c")
    Path(sys.argv[2]).write_text(generate(Path(sys.argv[1])), encoding="utf-8")
