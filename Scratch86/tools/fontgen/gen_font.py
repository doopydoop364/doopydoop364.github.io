"""Generate the 6x14 CP437 bitmap font used by the Scratch86 text renderer.

Development-time tool.  Rasterises Liberation Mono (SIL OFL 1.1) at high
resolution and area-downsamples each glyph to a 6x14 1-bit cell.  Output is a
JSON file checked into src/display/ so builds do not need the TTF or Pillow.

    python tools/fontgen/gen_font.py [path/to/LiberationMono-Regular.ttf]
"""
import json
import os
import sys

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

W, H = 6, 14
SS = 8                           # supersampling factor per target pixel
CTRL = "\u0000☺☻♥♦♣♠•◘○◙♂♀♪♫☼" \
       "►◄↕‼¶§▬↨↑↓→←∟↔▲▼"


def glyph_char(code):
    if code < 32:
        return CTRL[code]
    if code == 127:
        return "⌂"
    return bytes([code]).decode("cp437")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/liberation/LiberationMono-Regular.ttf"
    cmap = TTFont(path).getBestCmap()
    big_w, big_h = W * SS, H * SS
    # choose a size where the mono advance (0.6 em) is ~ 0.9 of the cell width
    size = int(big_w * 0.92 / 0.6)
    font = ImageFont.truetype(path, size)
    asc, desc = font.getmetrics()
    hb = font.getbbox('H')
    cap_h = hb[3] - hb[1]
    glyphs = []
    for code in range(256):
        ch = glyph_char(code)
        if code in (0, 32, 255) or ord(ch) not in cmap:
            glyphs.append([0] * H)
            continue
        img = Image.new("L", (big_w, big_h), 0)
        d = ImageDraw.Draw(img)
        bbox = font.getbbox(ch)
        x = (big_w - (bbox[2] - bbox[0])) // 2 - bbox[0]
        # vertical: stretch the em box over the cell height by rendering then resizing
        tall = Image.new("L", (big_w, asc + desc), 0)
        ImageDraw.Draw(tall).text((x, 0), ch, fill=255, font=font)
        # map [cap-top - margin, baseline + descender] onto the full cell height
        top = max(0, int(asc - cap_h - 0.05 * size))
        bot = min(asc + desc, int(asc + 0.24 * size))
        img = tall.crop((0, top, big_w, bot)).resize((big_w, big_h), Image.BOX)
        small = img.resize((W, H), Image.BOX)
        rows = []
        for y in range(H):
            bits = 0
            for xx in range(W):
                if small.getpixel((xx, y)) >= 90:
                    bits |= 1 << (W - 1 - xx)
            rows.append(bits)
        glyphs.append(rows)
    out = os.path.join(os.path.dirname(__file__), "..", "..", "src", "display", "font6x14.json")
    with open(out, "w") as f:
        json.dump({"cell": [W, H], "source": "Liberation Mono Regular (SIL OFL 1.1), downsampled", "glyphs": glyphs}, f)
    # ASCII preview for a few glyphs
    for code in (ord("A"), ord("g"), ord("@"), 0xDB, 0xC4):
        print(code, glyph_char(code))
        for r in glyphs[code]:
            print("".join("#" if r >> (W - 1 - i) & 1 else "." for i in range(W)))


if __name__ == "__main__":
    main()
