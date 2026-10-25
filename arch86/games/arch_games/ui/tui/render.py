"""Pure renderer: model + terminal size + capabilities -> rows of (text, style) spans. Terminals never see raw data."""

from dataclasses import dataclass

from ..terminal import clean, fit
from .model import SECTIONS, GAME_SECTIONS

MIN_WIDTH, MIN_HEIGHT = 20, 6


@dataclass
class Caps:
    color: bool = True
    unicode: bool = True

    @property
    def hline(self):
        return "─" if self.unicode else "-"

    @property
    def vline(self):
        return "│" if self.unicode else "|"

    @property
    def cursor(self):
        return "▶" if self.unicode else ">"

    @property
    def fav(self):
        return "★" if self.unicode else "*"


def pad(text, width, caps):
    text = fit(text, width, not caps.unicode)
    from unicodedata import east_asian_width, combining

    used = sum(0 if combining(c) else 2 if east_asian_width(c) in {"W", "F"} else 1 for c in text)
    return text + " " * max(width - used, 0)


def hints(model):
    row = model.current()
    base = ["/ search", "Enter details", "Tab section", "? help", "q quit"]
    extra = [f"{k} {d}" for k, (d, _) in sorted((row.actions if row else {}).items())][:4]
    return "  ".join(extra + base)


def tabs_line(model, width, caps):
    if width < 60:
        return "[{}/{}] {}  (Tab: next)".format(model.section + 1, len(SECTIONS), model.name)
    return " ".join(("[%s]" % n) if i == model.section else " %s " % n for i, n in enumerate(SECTIONS))


def window(items, cursor, height):
    start = max(0, min(cursor - height // 2, max(len(items) - height, 0)))
    return start, items[start : start + height]


def render(model, width, height, caps=None, title="ARCH GAMES 3.0", status=""):
    """Return a list of lines; each line is a list of (text, tone) spans whose lengths fit width."""
    caps = caps or Caps()
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        msg = fit("Too small: need {}x{}".format(MIN_WIDTH, MIN_HEIGHT), width, True)
        return [[(msg, "bad")]]
    if model.help_open:
        return help_screen(model, width, height, caps)
    lines = []
    lines.append([(pad(" " + title + "  " + status, width, caps), "title")])
    if height >= 8:
        lines.append([(pad(tabs_line(model, width, caps), width, caps), "tabs")])
    body_h = height - len(lines) - 2
    if model.searching or model.search:
        lines.append([(pad("Search: " + clean(model.search) + ("_" if model.searching else ""), width, caps), "key")])
        body_h -= 1
    split = width >= 96 and not model.detail_open
    if model.detail_open:
        body = detail_lines(model, width, body_h, caps)
    elif split:
        left_w = width * 5 // 11
        left = list_lines(model, left_w, body_h, caps)
        right = preview_lines(model, width - left_w - 1, body_h, caps)
        body = [l + [(caps.vline, "dim")] + r for l, r in zip(left, right)]
    else:
        body = list_lines(model, width, body_h, caps)
    lines += body[:max(body_h, 0)]
    lines.append([(pad(model.message or status_line(model), width, caps), "warn" if model.message else "dim")])
    lines.append([(pad(hints(model), width, caps), "key")])
    return lines[:height]


def status_line(model):
    n = len(model.rows)
    return "{} entr{} in {}".format(n, "y" if n == 1 else "ies", model.name) + (" (all shown)" if model.show_all and model.name in GAME_SECTIONS else "")


def list_lines(model, width, height, caps):
    if not model.rows:
        empty = model.provider.empty_text(model.name)
        return [[(pad(empty, width, caps), "dim")]] + [[(" " * width, "normal")] for _ in range(max(height - 1, 0))]
    start, view = window(model.rows, model.cursor, height)
    out = []
    for i, row in enumerate(view, start):
        selected = i == model.cursor
        mark = caps.cursor if selected else " "
        fav = caps.fav if row.favorite else " "
        tag = row.tag
        label_w = max(width - len(tag) - 5, 4)
        text = pad("{}{} {}".format(mark, fav, row.label), label_w + 3, caps)
        out.append([(text, "sel" if selected else "normal"), (pad(" " + tag, width - label_w - 3, caps), "sel" if selected else row.tone)])
    while len(out) < height:
        out.append([(" " * width, "normal")])
    return out


def preview_lines(model, width, height, caps):
    row = model.current()
    src = row.detail if row else []
    out = [[(pad(clean(t), width, caps), tone)] for t, tone in src[:height]]
    while len(out) < height:
        out.append([(" " * width, "normal")])
    return out


def detail_lines(model, width, height, caps):
    row = model.current()
    if not row:
        return [[(pad("Nothing selected", width, caps), "dim")]]
    out = [[(pad(clean(row.label), width, caps), "title")], [(caps.hline * width, "dim")]]
    for text, tone in row.detail:
        out.append([(pad(clean(text), width, caps), tone)])
    out.append([(" " * width, "normal")])
    for key, (desc, _) in sorted(row.actions.items()):
        out.append([(pad("  [{}] {}".format(key, desc), width, caps), "key")])
    out.append([(pad("  [Esc] back", width, caps), "key")])
    out = out[:height]
    while len(out) < height:
        out.append([(" " * width, "normal")])
    return out


HELP = [
    ("Navigation", ""),
    ("  Up/Down  j/k", "move; PgUp/PgDn scroll; Home/End"),
    ("  Tab / Shift-Tab, 1-9", "switch section (also Left/Right)"),
    ("  Enter / Esc", "open details / go back"),
    ("  /", "search (Enter keeps, Esc clears)"),
    ("Actions (shown at the bottom for the selected row)", ""),
    ("  l launch  i install  d download", "games"),
    ("  s settings  w wine status  x find executable", "games"),
    ("  a", "show/hide unverified GameVault entries"),
    ("  r", "refresh;   ?  this help;   q  quit"),
    ("", ""),
    ("Games start in your graphical session (X11/Wayland); browsing needs none.", ""),
]


def help_screen(model, width, height, caps):
    lines = [[(pad(" Keyboard shortcuts (any key closes)", width, caps), "title")]]
    for left, right in HELP:
        lines.append([(pad(left + ("  " + right if right else ""), width, caps), "normal")])
    return lines[:height]
