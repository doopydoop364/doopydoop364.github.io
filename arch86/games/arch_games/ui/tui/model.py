"""Pure TUI state machine: keys and mouse events in, state and optional actions out. No curses, easy to test."""

from dataclasses import dataclass, field
import time

SECTIONS = ["Library", "Installed", "GameVault", "Downloads", "Compatibility", "Wine", "Audio", "Settings", "Diagnostics"]
GAME_SECTIONS = {"Library", "Installed", "GameVault"}
# Whether each game list starts by showing everything. The dedicated GameVault tab shows every game (unknown ones are labelled,
# not hidden); the general Library hides unverified GameVault entries until you press `a`.
DEFAULT_SHOW_ALL = {"Library": False, "Installed": True, "GameVault": True}
WHEEL_STEP = 3
DOUBLE_CLICK_SECONDS = 0.5

# (label shown, value stored in the compatibility record). Same values as `arch-games compat set`.
GAMEPLAY_OPTIONS = [("Works", "works"), ("Works with issues", "issues"), ("Broken", "fails"), ("Untested", "unverified")]
AUDIO_OPTIONS = [("Works", "works"), ("Works with issues", "issues"), ("Broken", "none"), ("Untested", "unknown")]
PANEL_FOCUS = ("gameplay", "audio", "save", "cancel")


@dataclass
class Row:
    key: str
    label: str
    tag: str = ""
    tone: str = "normal"  # ok | warn | bad | dim | normal
    detail: list = field(default_factory=list)  # [(text, tone)]
    actions: dict = field(default_factory=dict)  # key char -> (description, argv)
    favorite: bool = False
    installed: bool = False


@dataclass
class Action:
    kind: str  # run | quit | refresh
    argv: list = field(default_factory=list)
    refresh: bool = True


@dataclass
class Hit:
    """A clickable rectangle produced by the renderer for the frame that is on screen."""

    x0: int
    x1: int  # exclusive
    y0: int
    y1: int  # exclusive
    kind: str  # tab | tab_next | row | action | back | fb_option | fb_save | fb_cancel
    value: object = None

    def contains(self, x, y):
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


@dataclass
class Panel:
    game_id: str
    title: str
    stored_gameplay: str
    stored_audio: str
    gameplay: int
    audio: int
    focus: int = 0

    @property
    def dirty(self):
        return (GAMEPLAY_OPTIONS[self.gameplay][1], AUDIO_OPTIONS[self.audio][1]) != (self.stored_gameplay, self.stored_audio)


def option_index(options, value, default=-1):
    return next((i for i, (_, v) in enumerate(options) if v == value), default)


class Model:
    def __init__(self, provider, clock=time.monotonic):
        self.provider = provider
        self.clock = clock
        self.section = 0
        self.cursor = 0
        self.offset = 0
        self.search = ""
        self.searching = False
        self.detail_open = False
        self.help_open = False
        self.filters = dict(DEFAULT_SHOW_ALL)
        self.message = ""
        self.rows = []
        self.page = 10
        self.panel = None
        self.hits = []  # set by the front end from the frame that was just drawn
        self._last_click = (None, 0.0)
        self.reload()

    @property
    def name(self):
        return SECTIONS[self.section]

    @property
    def show_all(self):
        return self.filters.get(self.name, True)

    @show_all.setter
    def show_all(self, value):
        self.filters[self.name] = bool(value)

    def reload(self):
        self.rows = self.provider.rows(self.name, self.search, self.show_all)
        self.cursor = min(self.cursor, max(len(self.rows) - 1, 0))

    def current(self):
        return self.rows[self.cursor] if self.rows else None

    def switch(self, index):
        self.section = index % len(SECTIONS)
        self.cursor = self.offset = 0
        self.detail_open = False
        self.panel = None
        self.search = ""
        self.reload()

    def move(self, delta):
        if self.rows:
            self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))

    def section_actions(self):
        getter = getattr(self.provider, "section_actions", None)
        return getter(self.name) if getter else {}

    # -- keyboard -------------------------------------------------------------------------------------------------
    def handle_key(self, key):
        """key: 'up','down','pgup','pgdn','home','end','enter','esc','tab','btab','backspace' or one character."""
        self.message = ""
        if self.help_open:
            self.help_open = False
            return None
        if self.panel is not None:
            return self._panel_key(key)
        if self.searching:
            return self._search_key(key)
        simple = {"up": -1, "k": -1, "down": 1, "j": 1, "pgup": -self.page, "pgdn": self.page}
        if key in simple:
            self.move(simple[key])
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = max(len(self.rows) - 1, 0)
        elif key in {"tab", "right", "L"}:
            self.switch(self.section + 1)
        elif key in {"btab", "left", "H"}:
            self.switch(self.section - 1)
        elif key.isdigit() and key != "0" and int(key) <= len(SECTIONS):
            self.switch(int(key) - 1)
        elif key == "/":
            self.searching = True
        elif key in {"enter"}:
            if self.current():
                self.detail_open = True
        elif key in {"esc", "backspace", "b"}:
            if self.detail_open:
                self.detail_open = False
            elif self.search:
                self.search = ""
                self.reload()
            elif key == "esc":
                return None
        elif key == "?":
            self.help_open = True
        elif key == "a" and self.name in GAME_SECTIONS:
            self.show_all = not self.show_all
            self.reload()
            self.message = (
                "Showing every game; unknown/unverified ones are labelled" if self.show_all
                else "Hiding unverified and incompatible games (a shows them again)"
            )
        elif key == "v" and self.name in GAME_SECTIONS and self.current():
            self.open_panel()
        elif key == "r":
            self.provider.invalidate()
            self.reload()
            self.message = "Refreshed"
        elif key == "q":
            return Action("quit")
        else:
            return self._row_action(key)
        return None

    def _row_action(self, key):
        row = self.current()
        if row and key in row.actions:
            return Action("run", list(row.actions[key][1]))
        section = self.section_actions()
        if key in section:
            return Action("run", list(section[key][1]))
        if key.isprintable() and len(key) == 1 and key not in "ajkqrbvHL/?":
            self.message = "No action '{}' here (press ? for help)".format(key)
        return None

    def _search_key(self, key):
        if key == "enter":
            self.searching = False
        elif key == "esc":
            self.searching = False
            self.search = ""
        elif key == "backspace":
            self.search = self.search[:-1]
        elif len(key) == 1 and key.isprintable() and len(self.search) < 64:
            self.search += key
        self.cursor = self.offset = 0
        self.reload()
        return None

    # -- compatibility feedback panel -----------------------------------------------------------------------------
    def open_panel(self):
        row = self.current()
        state = getattr(self.provider, "feedback_state", None)
        if row is None or state is None:
            self.message = "Feedback is not available here"
            return
        stored = state(row.key)
        gameplay = option_index(GAMEPLAY_OPTIONS, stored["gameplay"], 3)
        audio = option_index(AUDIO_OPTIONS, stored["audio"], 3)
        self.panel = Panel(row.key, row.label, stored["gameplay"], stored["audio"], gameplay, audio)

    def _panel_key(self, key):
        p = self.panel
        if key in {"esc", "c"}:
            return self.cancel_panel()
        if key in {"down", "j", "tab"}:
            p.focus = (p.focus + 1) % len(PANEL_FOCUS)
        elif key in {"up", "k", "btab"}:
            p.focus = (p.focus - 1) % len(PANEL_FOCUS)
        elif key in {"left", "h"}:
            self._cycle(-1)
        elif key in {"right", "l", " "}:
            self._cycle(1)
        elif key == "s":
            return self.save_panel()
        elif key == "enter":
            if PANEL_FOCUS[p.focus] == "save":
                return self.save_panel()
            if PANEL_FOCUS[p.focus] == "cancel":
                return self.cancel_panel()
            p.focus += 1  # on an option row Enter moves on to the next group
        return None

    def _cycle(self, step):
        p = self.panel
        if PANEL_FOCUS[p.focus] == "gameplay":
            p.gameplay = (p.gameplay + step) % len(GAMEPLAY_OPTIONS)
        elif PANEL_FOCUS[p.focus] == "audio":
            p.audio = (p.audio + step) % len(AUDIO_OPTIONS)

    def save_panel(self):
        """Explicit save through the same compatibility service the CLI uses; nothing is written while navigating."""
        p = self.panel
        result = self.provider.save_feedback(p.game_id, GAMEPLAY_OPTIONS[p.gameplay][1], AUDIO_OPTIONS[p.audio][1])
        self.panel = None
        self.reload()
        self.message = "Saved: gameplay {}, audio {} - now {}".format(GAMEPLAY_OPTIONS[p.gameplay][0].lower(), AUDIO_OPTIONS[p.audio][0].lower(), result)
        return None

    def cancel_panel(self):
        self.panel = None
        self.message = "Cancelled; nothing was changed"
        return None

    # -- mouse ----------------------------------------------------------------------------------------------------
    def handle_mouse(self, kind, x, y):
        """kind: click | double | wheel_up | wheel_down. Coordinates are screen cells of the frame the hits came from."""
        self.message = ""
        if self.help_open:
            self.help_open = False
            return None
        if kind in {"wheel_up", "wheel_down"}:
            if self.panel is None and not self.searching:
                self.move(-WHEEL_STEP if kind == "wheel_up" else WHEEL_STEP)
            return None
        if kind not in {"click", "double"}:
            return None
        hit = next((h for h in reversed(self.hits) if h.contains(x, y)), None)
        if hit is None:
            return None
        if hit.kind == "tab":
            self.switch(hit.value)
        elif hit.kind == "tab_next":
            self.switch(self.section + 1)
        elif hit.kind == "row":
            return self._click_row(hit.value, kind == "double")
        elif hit.kind == "action":
            return self.handle_key(hit.value)
        elif hit.kind == "back":
            return self.handle_key("esc")
        elif hit.kind == "fb_option" and self.panel is not None:
            group, index = hit.value
            self.panel.focus = PANEL_FOCUS.index(group)
            if group == "gameplay":
                self.panel.gameplay = index
            else:
                self.panel.audio = index
        elif hit.kind == "fb_save" and self.panel is not None:
            return self.save_panel()
        elif hit.kind == "fb_cancel" and self.panel is not None:
            return self.cancel_panel()
        return None

    def _click_row(self, index, double):
        if not 0 <= index < len(self.rows):
            return None
        previous, then = self._last_click
        repeated = previous == (self.name, index) and self.clock() - then <= DOUBLE_CLICK_SECONDS
        self.cursor = index
        if double or repeated:
            self.detail_open = True
            self._last_click = (None, 0.0)
        else:
            self._last_click = ((self.name, index), self.clock())
        return None
