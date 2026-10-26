"""Pure renderer: model + terminal size + capabilities -> lines of (text, style) spans plus clickable regions.

Nothing here touches curses. `render_frame` returns the lines and the hit rectangles of exactly that frame, so mouse hit testing
is always correct for the current size and scroll position; `render` keeps returning just the lines."""

from dataclasses import dataclass, field

from ..terminal import clean, fit
from .model import AUDIO_OPTIONS, GAME_SECTIONS, GAMEPLAY_OPTIONS, PANEL_FOCUS, SECTIONS, Hit

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

    @property
    def radio_on(self):
        return "(•)" if self.unicode else "(*)"

    @property
    def radio_off(self):
        return "( )"


@dataclass
class Frame:
    lines: list
    hits: list = field(default_factory=list)


def text_width(text):
    from unicodedata import combining, east_asian_width

    return sum(0 if combining(c) else 2 if east_asian_width(c) in {"W", "F"} else 1 for c in text)


def pad(text, width, caps):
    text = fit(text, width, not caps.unicode)
    return text + " " * max(width - text_width(text), 0)


def hints(model):
    row = model.current()
    base = ["/ search", "Enter details", "Tab section", "? help", "q quit"]
    extra = [f"{k} {d}" for k, (d, _) in sorted((row.actions if row else {}).items())][:3]
    if model.name in GAME_SECTIONS and row:
        extra.append("v feedback")
        extra.append("a all/filter")
    extra += [f"{k} {d}" for k, (d, _) in sorted(model.section_actions().items())][:2]
    return "  ".join(extra + base)


def tab_layout(model, width):
    """(text, [(x0, x1, index)]) for the tab bar; narrow terminals show a single clickable 'next section' label."""
    if width < 60:
        return "[{}/{}] {}  (Tab: next)".format(model.section + 1, len(SECTIONS), model.name), []
    text, spans, x = "", [], 0
    for i, name in enumerate(SECTIONS):
        label = ("[%s]" % name) if i == model.section else " %s " % name
        spans.append((x, x + len(label), i))
        text += label + " "
        x += len(label) + 1
    return text, spans


def window(items, cursor, height):
    start = max(0, min(cursor - height // 2, max(len(items) - height, 0)))
    return start, items[start : start + height]


def render(model, width, height, caps=None, title="ARCH GAMES 3.0", status=""):
    return render_frame(model, width, height, caps, title, status).lines


def render_frame(model, width, height, caps=None, title="ARCH GAMES 3.0", status=""):
    caps = caps or Caps()
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        msg = fit("Too small: need {}x{}".format(MIN_WIDTH, MIN_HEIGHT), width, True)
        return Frame([[(msg, "bad")]])
    if model.help_open:
        return Frame(help_screen(model, width, height, caps))
    lines, hits = [], []
    lines.append([(pad(" " + title + "  " + status, width, caps), "title")])
    if height >= 8:
        text, spans = tab_layout(model, width)
        y = len(lines)
        lines.append([(pad(text, width, caps), "tabs")])
        if spans:
            hits += [Hit(x0, min(x1, width), y, y + 1, "tab", i) for x0, x1, i in spans]
        else:
            hits.append(Hit(0, width, y, y + 1, "tab_next"))
    body_h = height - len(lines) - 2
    if model.searching or model.search:
        lines.append([(pad("Search: " + clean(model.search) + ("_" if model.searching else ""), width, caps), "key")])
        body_h -= 1
    top = len(lines)
    if model.panel is not None:
        body, body_hits = panel_lines(model, width, body_h, caps, top)
    elif model.detail_open:
        body, body_hits = detail_lines(model, width, body_h, caps, top)
    elif width >= 96:
        left_w = width * 5 // 11
        left, body_hits = list_lines(model, left_w, body_h, caps, top)
        right = preview_lines(model, width - left_w - 1, body_h, caps)
        body = [lft + [(caps.vline, "dim")] + rgt for lft, rgt in zip(left, right)]
    else:
        body, body_hits = list_lines(model, width, body_h, caps, top)
    hits += body_hits
    lines += body[: max(body_h, 0)]
    lines.append([(pad(model.message or status_line(model), width, caps), "warn" if model.message else "dim")])
    lines.append([(pad(hints(model), width, caps), "key")])
    hits = [h for h in hits if h.y0 < min(height, len(lines))]
    return Frame(lines[:height], hits)


def status_line(model):
    text = getattr(model.provider, "status_text", None)
    if text:
        return text(model.name, len(model.rows), model.show_all, model.search)
    n = len(model.rows)
    return "{} entr{} in {}".format(n, "y" if n == 1 else "ies", model.name)


def blank(width):
    return [(" " * width, "normal")]


def list_lines(model, width, height, caps, top):
    if not model.rows:
        empty = model.provider.empty_text(model.name, model.search, model.show_all) if _accepts_state(model.provider) else model.provider.empty_text(model.name)
        wrapped = wrap(empty, width)
        out = [[(pad(t, width, caps), "dim")] for t in wrapped[:height]]
        return out + [blank(width) for _ in range(max(height - len(out), 0))], []
    start, view = window(model.rows, model.cursor, height)
    out, hits = [], []
    for i, row in enumerate(view, start):
        selected = i == model.cursor
        mark = caps.cursor if selected else " "
        fav = caps.fav if row.favorite else " "
        tag = row.tag
        label_w = max(width - len(tag) - 5, 4)
        text = pad("{}{} {}".format(mark, fav, row.label), label_w + 3, caps)
        out.append([(text, "sel" if selected else "normal"), (pad(" " + tag, width - label_w - 3, caps), "sel" if selected else row.tone)])
        hits.append(Hit(0, width, top + len(out) - 1, top + len(out), "row", i))
    while len(out) < height:
        out.append(blank(width))
    return out, hits


def _accepts_state(provider):
    import inspect

    try:
        return len(inspect.signature(provider.empty_text).parameters) >= 3
    except (TypeError, ValueError):
        return False


def wrap(text, width):
    words, lines, current = clean(text).split(), [], ""
    for word in words:
        if current and text_width(current) + 1 + text_width(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    return lines + ([current] if current else [])


def preview_lines(model, width, height, caps):
    row = model.current()
    src = row.detail if row else []
    out = [[(pad(clean(t), width, caps), tone)] for t, tone in src[:height]]
    while len(out) < height:
        out.append(blank(width))
    return out


def detail_lines(model, width, height, caps, top):
    row = model.current()
    if not row:
        return [[(pad("Nothing selected", width, caps), "dim")]], [Hit(0, width, top, top + 1, "back")]
    out = [[(pad(clean(row.label), width, caps), "title")], [(caps.hline * width, "dim")]]
    for text, tone in row.detail:
        out.append([(pad(clean(text), width, caps), tone)])
    out.append(blank(width))
    hits = []
    actions = sorted(row.actions.items())
    if model.name in GAME_SECTIONS:
        actions.append(("v", ("compatibility feedback", None)))
    for key, (desc, _) in actions:
        out.append([(pad("  [{}] {}".format(key, desc), width, caps), "key")])
        hits.append(Hit(0, width, top + len(out) - 1, top + len(out), "action", key))
    out.append([(pad("  [Esc] back", width, caps), "key")])
    hits.append(Hit(0, width, top + len(out) - 1, top + len(out), "back"))
    out = out[:height]
    hits = [h for h in hits if h.y0 < top + height]
    while len(out) < height:
        out.append(blank(width))
    return out, hits


def panel_lines(model, width, height, caps, top):
    """The compatibility feedback panel: gameplay and audio radio groups plus Save / Cancel. Click or use the keyboard."""
    p = model.panel
    out, hits = [], []
    focus = PANEL_FOCUS[p.focus]

    def add(spans, tone="normal"):
        out.append(spans if isinstance(spans, list) else [(pad(spans, width, caps), tone)])

    add([(pad("Compatibility feedback: " + clean(p.title), width, caps), "title")])
    add("Stored: gameplay {}, audio {} (this machine only)".format(_label(GAMEPLAY_OPTIONS, p.stored_gameplay, "unverified"), _label(AUDIO_OPTIONS, p.stored_audio, "unknown")), "dim")
    horizontal = width >= 72
    for group, options, selected in (("gameplay", GAMEPLAY_OPTIONS, p.gameplay), ("audio", AUDIO_OPTIONS, p.audio)):
        marker = caps.cursor if focus == group else " "
        if horizontal:
            x, cells = 12, []
            y = top + len(out)
            for i, (label, _) in enumerate(options):
                cell = "{} {}".format(caps.radio_on if i == selected else caps.radio_off, label)
                hits.append(Hit(x, min(x + len(cell), width), y, y + 1, "fb_option", (group, i)))
                cells.append(cell)
                x += len(cell) + 2
            add([(pad("{} {:<9}".format(marker, group.capitalize() + ":") + "  ".join(cells), width, caps), "sel" if focus == group else "normal")])
        else:
            add([(pad("{} {}:".format(marker, group.capitalize()), width, caps), "sel" if focus == group else "normal")])
            for i, (label, _) in enumerate(options):
                y = top + len(out)
                add("    {} {}".format(caps.radio_on if i == selected else caps.radio_off, label), "normal")
                hits.append(Hit(0, width, y, y + 1, "fb_option", (group, i)))
    add("")
    y = top + len(out)
    save = "[ Save ]" if focus != "save" else "[>Save<]"
    cancel = "[ Cancel ]" if focus != "cancel" else "[>Cancel<]"
    add([(pad("  " + save + "  " + cancel + ("   (unsaved changes)" if p.dirty else ""), width, caps), "key")])
    hits.append(Hit(2, 2 + len(save), y, y + 1, "fb_save"))
    hits.append(Hit(4 + len(save), 4 + len(save) + len(cancel), y, y + 1, "fb_cancel"))
    add("Up/Down or Tab: move   Left/Right or Space: choose   s: save   Esc: cancel", "dim")
    out = out[:height]
    hits = [h for h in hits if h.y0 < top + height]
    while len(out) < height:
        out.append(blank(width))
    return out, hits


def _label(options, value, default):
    return next((label for label, v in options if v == value), dict((v, l) for l, v in options).get(default, "Untested"))


HELP = [
    ("Navigation", ""),
    ("  Up/Down  j/k", "move; PgUp/PgDn scroll; Home/End"),
    ("  Tab / Shift-Tab, 1-9", "switch section (also Left/Right)"),
    ("  Enter / Esc", "open details / go back"),
    ("  /", "search (Enter keeps, Esc clears)"),
    ("Mouse (terminals that report it; the keyboard always works)", ""),
    ("  click", "select a game or choose a section tab"),
    ("  double-click / click twice", "open the game's details"),
    ("  wheel", "scroll the list;  click [key] lines in details to act"),
    ("Games", ""),
    ("  l launch  i install  d download", "run the CLI command, then return here"),
    ("  s settings  w wine status  x find executable", ""),
    ("  v", "compatibility feedback (gameplay and audio) for this machine"),
    ("  a", "toggle the compatibility filter: GameVault and Installed start by showing every game,"),
    ("", "Library starts hiding unverified ones; labels show Unknown rather than hiding"),
    ("  S (GameVault tab)", "sync the library now (the only network use; tabs never sync)"),
    ("  r", "refresh from local data;   ?  this help;   q  quit"),
    ("", ""),
    ("Games start in your graphical session (X11/Wayland); browsing needs none.", ""),
]


def help_screen(model, width, height, caps):
    lines = [[(pad(" Keyboard and mouse help (any key or click closes)", width, caps), "title")]]
    for left, right in HELP:
        lines.append([(pad(left + ("  " + right if right else ""), width, caps), "normal")])
    return lines[:height]
