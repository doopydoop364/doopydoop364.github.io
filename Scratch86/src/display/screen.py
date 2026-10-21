"""Text-mode screen (Screen sprite): composes UI_BUF over guest video memory
and stamps only the cells whose composed value changed, using the Pen
extension.  Cell = 6x14 stage pixels, 80x25 cells, centred vertically.

Costume layout of the Screen sprite (1-based costume numbers):
    1..16                       solid background colours (cell fill)
    17 + fg*256 + code          glyph `code` in foreground colour `fg`

The glyph font is a placeholder 6x14 rasterisation of Liberation Mono (see
tools/fontgen); real VGA ROM fonts are a later milestone.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403

HERE = os.path.dirname(__file__)
PALETTE = ["000000", "0000AA", "00AA00", "00AAAA", "AA0000", "AA00AA", "AA5500", "AAAAAA",
           "555555", "5555FF", "55FF55", "55FFFF", "FF5555", "FF55FF", "FFFF55", "FFFFFF"]
CELL_W, CELL_H = 6, 14
TOP = 175               # y of the top edge of row 0 (stage is 360 high; 25*14 = 350)
LEFT = -240


def _bg_svg(color):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="6" height="14" viewBox="0 0 6 14">'
            '<rect width="6" height="14" fill="#%s"/></svg>' % color)


def _glyph_svg(rows, color):
    rects = []
    for y, bits in enumerate(rows):
        x = 0
        while x < CELL_W:
            if bits >> (CELL_W - 1 - x) & 1:
                x0 = x
                while x < CELL_W and bits >> (CELL_W - 1 - x) & 1:
                    x += 1
                rects.append('<rect x="%d" y="%d" width="%d" height="1"/>' % (x0, y, x - x0))
            else:
                x += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="6" height="14" viewBox="0 0 6 14" '
            'shape-rendering="crispEdges"><g fill="#%s">%s</g></svg>' % (color, "".join(rects)))


GEAR_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
            '<circle cx="7" cy="7" r="3.2" fill="none" stroke="#cccccc" stroke-width="1.6"/>'
            '<g stroke="#cccccc" stroke-width="1.8"><path d="M7 0.5v2.4M7 11.1v2.4M0.5 7h2.4M11.1 7h2.4'
            'M2.4 2.4l1.7 1.7M9.9 9.9l1.7 1.7M2.4 11.6l1.7-1.7M9.9 4.1l1.7-1.7"/></g></svg>')


def build(p, S):
    p.extensions.add("pen")
    font = json.load(open(os.path.join(HERE, "font6x14.json")))["glyphs"]
    S.SCR_SHADOW = p.list("SCR_SHADOW (composed cell last drawn)", [-1] * 2000)
    S.SCR_I = p.var("scr i")
    S.SCR_V = p.var("scr value")
    S.SCR_CH = p.var("scr char")
    S.SCR_AT = p.var("scr attr")

    scr = p.sprite("Screen", visible=False)
    for c in range(16):
        scr.costume("bg%X" % c, _bg_svg(PALETTE[c]), 0, 0)
    for fg in range(16):
        for code in range(256):
            scr.costume("fg%X_%02X" % (fg, code), _glyph_svg(font[code], PALETTE[fg]), 0, 0)

    pr = scr.procedure("scr_frame", [])
    with pr.body() as (b,):
        b.set(S.SCR_I, 0)
        with b.repeat(2000) as w:
            w.change(S.SCR_I, 1)
            w.set(S.SCR_V, S.UI_BUF[S.SCR_I])
            with w.if_(S.SCR_V < 0) as t:
                # text page at B8000: char at +2*(i-1), attribute at +2*(i-1)+1  (RAM_11 = B0000..BFFFF)
                t.set(S.SCR_V, S.RAM[11][0x8000 + S.SCR_I * 2 - 1] + 256 * S.RAM[11][0x8000 + S.SCR_I * 2])
            with w.if_(eq(S.SCREEN_ON, 0)) as t:
                t.set(S.SCR_V, 0)
            with w.if_(ne(S.SCR_V, S.SCR_SHADOW[S.SCR_I])) as t:
                t.replace(S.SCR_SHADOW, S.SCR_I, S.SCR_V)
                t.set(S.SCR_CH, S.SCR_V % 256)
                t.set(S.SCR_AT, floor(S.SCR_V / 256) % 256)
                t.raw("motion_gotoxy", {"X": LEFT + CELL_W * ((S.SCR_I - 1) % 80),
                                        "Y": TOP - CELL_H * floor((S.SCR_I - 1) / 80)})
                t.switch_costume(floor(S.SCR_AT / 16) % 8 + 1)
                t.raw("pen_stamp")
                with t.if_(S.SCR_CH > 32) as u:
                    u.switch_costume(17 + (S.SCR_AT % 16) * 256 + S.SCR_CH)
                    u.raw("pen_stamp")

    # NOTE: every loop must live in a warp procedure.  A loop in a plain script
    # advances one iteration per frame in Scratch (2000 iterations = 66 s).
    init = scr.procedure("scr_init", [])
    with init.body() as (b,):
        b.delete_all(S.SCR_SHADOW)
        with b.repeat(2000) as w:
            w.add(S.SCR_SHADOW, -1)

    with scr.on_flag() as b:
        b.raw("looks_hide")
        b.raw("pen_clear")
        b.call(init)
        with b.forever() as w:
            w.call(pr)

    gear = p.sprite("Settings", x=232, y=173)
    gear.costume("gear", GEAR_SVG, 7, 7)
    with gear.on_flag() as b:
        b.raw("looks_seteffectto", {"VALUE": 55}, {"EFFECT": ("GHOST", None)})
        with b.forever() as w:
            with w.if_(eq(S.MENU_OPEN, 1)) as t:
                t.raw("looks_hide")
            with w.else_() as t:
                t.raw("looks_show")
    with gear.on_click() as b:
        b.set(S.MENU_OPEN, 1)
    return scr
