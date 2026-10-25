"""Pure TUI state machine: keys in, state and optional actions out. No curses, easy to test."""

from dataclasses import dataclass, field

SECTIONS = ["Library", "Installed", "GameVault", "Downloads", "Compatibility", "Wine", "Audio", "Settings", "Diagnostics"]
GAME_SECTIONS = {"Library", "Installed", "GameVault"}


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


class Model:
    def __init__(self, provider):
        self.provider = provider
        self.section = 0
        self.cursor = 0
        self.offset = 0
        self.search = ""
        self.searching = False
        self.detail_open = False
        self.help_open = False
        self.show_all = False
        self.message = ""
        self.rows = []
        self.page = 10
        self.reload()

    @property
    def name(self):
        return SECTIONS[self.section]

    def reload(self):
        self.rows = self.provider.rows(self.name, self.search, self.show_all)
        self.cursor = min(self.cursor, max(len(self.rows) - 1, 0))

    def current(self):
        return self.rows[self.cursor] if self.rows else None

    def switch(self, index):
        self.section = index % len(SECTIONS)
        self.cursor = self.offset = 0
        self.detail_open = False
        self.search = ""
        self.reload()

    def move(self, delta):
        if self.rows:
            self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))

    def handle_key(self, key):
        """key: 'up','down','pgup','pgdn','home','end','enter','esc','tab','btab','backspace' or one character."""
        self.message = ""
        if self.help_open:
            self.help_open = False
            return None
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
            self.message = "Showing all entries" if self.show_all else "Hiding unverified GameVault entries"
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
        if row and key.isprintable() and len(key) == 1 and key not in "ajkqrbHL/?":
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
