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


@dataclass
class LaunchPanel:
    """Editable copy of a game's launch settings. Nothing is stored until Save."""

    game_id: str
    title: str
    original: dict
    draft: dict
    status: dict
    profile: str
    cursor: int = 0
    input: dict = None  # {"kind": arg_add|arg_edit|env_add|env_edit|profile_new, "buffer": str, "index": int|None}
    notice: str = ""
    discard_warned: bool = False

    @property
    def dirty(self):
        return self.draft != self.original


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
        self.launch_panel = None
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
        self.launch_panel = None
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
        if self.launch_panel is not None:
            return self._launch_key(key)
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
        elif key == "o" and self.name in GAME_SECTIONS and self.current():
            self.open_launch_panel()
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
        if key.isprintable() and len(key) == 1 and key not in "ajkoqrbvHL/?":
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
        try:
            result = self.provider.save_feedback(p.game_id, GAMEPLAY_OPTIONS[p.gameplay][1], AUDIO_OPTIONS[p.audio][1])
        except Exception as exc:  # a failed write must not crash the TUI or lose the panel's choices
            self.message = "Could not save: {}".format(str(exc)[:100] or type(exc).__name__)
            return None
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
            if self.launch_panel is not None:
                if self.launch_panel.input is None:
                    self._lp_move(-WHEEL_STEP if kind == "wheel_up" else WHEEL_STEP)
            elif self.panel is None and not self.searching:
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
        elif hit.kind == "lp_item" and self.launch_panel is not None:
            return self._lp_click(hit.value, kind == "double")
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


    # -- launch settings panel -----------------------------------------------------------------------------------
    def open_launch_panel(self):
        row = self.current()
        loader = getattr(self.provider, "launch_settings", None)
        if row is None or loader is None:
            self.message = "Launch settings are not available here"
            return
        loaded = loader(row.key)
        if loaded is None:
            self.message = "No launcher is registered yet: press x to find the executable first"
            return
        import copy

        from ...runtime import profiles

        record = loaded["record"]
        self.launch_panel = LaunchPanel(loaded["state_id"], row.label, copy.deepcopy(record), copy.deepcopy(record), loaded["status"], profiles.default_name(record))

    def lp_items(self):
        """The panel's lines: info (not selectable) and selectable items, built from the current draft."""
        from ...runtime import profiles

        p = self.launch_panel
        cfg = profiles.get(p.draft, p.profile)
        items = [{"kind": "info", "text": "Executable: {}".format(cfg["executable"]), "tone": "bad" if p.status.get("status") == "launcher-invalid" else "normal"},
                 {"kind": "info", "text": "Runtime: {}   Status: {}".format("native Linux (no Wine)" if cfg["runner"] == "native" else cfg["runner"], p.status.get("label", "")), "tone": "dim"}]
        if p.status.get("detail"):
            items.append({"kind": "info", "text": p.status["detail"], "tone": "warn"})
        default = profiles.default_name(p.draft)
        items.append({"kind": "profile", "text": "Profile: < {} >{}   ({}/{})".format(p.profile, "  [default]" if p.profile == default else "", profiles.names(p.draft).index(p.profile) + 1, len(profiles.names(p.draft)))})
        items.append({"kind": "info", "text": "Arguments (passed one by one, never through a shell):", "tone": "dim"})
        for i, a in enumerate(cfg["arguments"]):
            items.append({"kind": "arg", "index": i, "text": "  {}. {}".format(i + 1, a)})
        items.append({"kind": "arg_add", "text": "  [+ add argument]"})
        items.append({"kind": "info", "text": "Environment overrides:", "tone": "dim"})
        for name in sorted(cfg["environment"]):
            items.append({"kind": "env", "name": name, "text": "  {}={}".format(name, cfg["environment"][name])})
        items.append({"kind": "env_add", "text": "  [+ add variable NAME=VALUE]"})
        items.append({"kind": "info", "text": "Command: " + profiles.command_preview(cfg["executable"], cfg["arguments"], cfg["environment"], cfg["runner"]), "tone": "key"})
        items.append({"kind": "set_default", "text": "[ Make '{}' the default profile ]".format(p.profile), "button": True})
        items.append({"kind": "new_profile", "text": "[ New profile (copy of '{}') ]".format(p.profile), "button": True})
        if p.profile != profiles.DEFAULT:
            items.append({"kind": "delete_profile", "text": "[ Delete profile '{}' ]".format(p.profile), "button": True})
        items.append({"kind": "rediscover", "text": "[ Rediscover executable (settings are kept) ]", "button": True})
        items.append({"kind": "save", "text": "[ Save ]" + ("  (unsaved changes)" if p.dirty else ""), "button": True})
        items.append({"kind": "cancel", "text": "[ Cancel ]", "button": True})
        return items

    SELECTABLE = {"profile", "arg", "arg_add", "env", "env_add", "set_default", "new_profile", "delete_profile", "rediscover", "save", "cancel"}

    def _lp_selectable(self):
        return [i for i, item in enumerate(self.lp_items()) if item["kind"] in self.SELECTABLE]

    def _lp_move(self, delta):
        p = self.launch_panel
        sel = self._lp_selectable()
        if not sel:
            return
        pos = sel.index(p.cursor) if p.cursor in sel else 0
        p.cursor = sel[max(0, min(len(sel) - 1, pos + delta))]

    def _lp_item(self):
        items = self.lp_items()
        p = self.launch_panel
        if p.cursor >= len(items) or items[p.cursor]["kind"] not in self.SELECTABLE:
            sel = self._lp_selectable()
            p.cursor = sel[0] if sel else 0
        return self.lp_items()[p.cursor]

    def _launch_key(self, key):
        p = self.launch_panel
        if p.input is not None:
            return self._lp_input_key(key)
        item = self._lp_item()
        if key != "esc":
            p.discard_warned = False
        if key in {"down", "j", "tab"}:
            self._lp_move(1)
        elif key in {"up", "k", "btab"}:
            self._lp_move(-1)
        elif key in {"pgdn"}:
            self._lp_move(8)
        elif key in {"pgup"}:
            self._lp_move(-8)
        elif key in {"left", "h", "["}:
            self._lp_profile(-1)
        elif key in {"right", "l", "]"}:
            self._lp_profile(1)
        elif key in {"d", "x"} and item["kind"] in {"arg", "env"}:
            self._lp_delete(item)
        elif key == "a":
            self._lp_begin("arg_add", "")
        elif key == "e":
            self._lp_begin("env_add", "")
        elif key == "s":
            return self.save_launch_panel()
        elif key == "enter":
            return self._lp_activate(item)
        elif key == "esc":
            return self.cancel_launch_panel(force=p.discard_warned)
        return None

    def _lp_profile(self, step):
        from ...runtime import profiles

        p = self.launch_panel
        names = profiles.names(p.draft)
        p.profile = names[(names.index(p.profile) + step) % len(names)]
        p.cursor = 3 if p.cursor >= len(self.lp_items()) else p.cursor

    def _lp_begin(self, kind, buffer, index=None):
        self.launch_panel.input = {"kind": kind, "buffer": buffer, "index": index}
        self.launch_panel.notice = ""

    def _lp_activate(self, item):
        from ...runtime import profiles

        p = self.launch_panel
        kind = item["kind"]
        if kind == "profile":
            self._lp_profile(1)
        elif kind == "arg":
            self._lp_begin("arg_edit", profiles.get(p.draft, p.profile)["arguments"][item["index"]], item["index"])
        elif kind == "arg_add":
            self._lp_begin("arg_add", "")
        elif kind == "env":
            self._lp_begin("env_edit", "{}={}".format(item["name"], profiles.get(p.draft, p.profile)["environment"][item["name"]]), item["name"])
        elif kind == "env_add":
            self._lp_begin("env_add", "")
        elif kind == "new_profile":
            self._lp_begin("profile_new", "")
        elif kind == "set_default":
            self._lp_apply(lambda d: profiles.set_default(d, p.profile), "'{}' will be the default profile (unsaved)".format(p.profile))
        elif kind == "delete_profile":
            name = p.profile
            self._lp_apply(lambda d: profiles.delete(d, name), "Profile '{}' removed (unsaved)".format(name))
            p.profile = profiles.default_name(p.draft)
        elif kind == "rediscover":
            return Action("run", ["launcher", "rediscover", p.game_id])
        elif kind == "save":
            return self.save_launch_panel()
        elif kind == "cancel":
            return self.cancel_launch_panel(force=True)
        return None

    def _lp_apply(self, change, notice=""):
        from ...config import AppError

        p = self.launch_panel
        try:
            p.draft = change(p.draft)
            p.notice = notice
        except AppError as exc:
            p.notice = str(exc)

    def _lp_delete(self, item):
        from ...runtime import profiles

        p = self.launch_panel
        cfg = profiles.get(p.draft, p.profile)
        if item["kind"] == "arg":
            args = cfg["arguments"][: item["index"]] + cfg["arguments"][item["index"] + 1:]
            self._lp_apply(lambda d: profiles.edit(d, p.profile, arguments=args), "Argument removed (unsaved)")
        else:
            self._lp_apply(lambda d: profiles.edit(d, p.profile, remove_env=[item["name"]]), "{} removed (unsaved)".format(item["name"]))
        self._lp_move(0)

    def _lp_input_key(self, key):
        p = self.launch_panel
        entry = p.input
        if key == "esc":
            p.input = None
            p.notice = "Edit cancelled"
        elif key == "backspace":
            entry["buffer"] = entry["buffer"][:-1]
        elif key == "enter":
            self._lp_commit(entry)
        elif len(key) == 1 and key.isprintable() and len(entry["buffer"]) < 512:
            entry["buffer"] += key
        return None

    def _lp_commit(self, entry):
        from ...config import AppError
        from ...runtime import profiles

        p = self.launch_panel
        text, kind = entry["buffer"], entry["kind"]
        try:
            cfg = profiles.get(p.draft, p.profile)
            if kind in {"arg_add", "arg_edit"}:
                if not text:
                    raise AppError("An argument cannot be empty")
                args = list(cfg["arguments"])
                if kind == "arg_add":
                    args.append(text)
                else:
                    args[entry["index"]] = text
                p.draft = profiles.edit(p.draft, p.profile, arguments=args)
            elif kind in {"env_add", "env_edit"}:
                name, sep, value = text.partition("=")
                if not sep:
                    raise AppError("Write it as NAME=VALUE")
                remove = [entry["index"]] if kind == "env_edit" and entry["index"] != name else []
                p.draft = profiles.edit(p.draft, p.profile, environment_changes={name.strip(): value}, remove_env=remove)  # sensitive names are refused here
            else:
                p.draft = profiles.create(p.draft, text.strip(), p.profile)
                p.profile = text.strip()
            p.input = None
            p.notice = "Changed (unsaved): press s to save"
        except AppError as exc:
            p.notice = str(exc)  # the input stays open so the text can be corrected

    def _lp_click(self, index, double):
        p = self.launch_panel
        if p.input is not None:
            return None
        items = self.lp_items()
        if not 0 <= index < len(items) or items[index]["kind"] not in self.SELECTABLE:
            return None
        item = items[index]
        p.cursor = index
        if item.get("button") or item["kind"] in {"arg_add", "env_add", "profile"}:
            return self._lp_activate(item)
        previous, then = self._last_click
        repeated = previous == ("lp", index) and self.clock() - then <= DOUBLE_CLICK_SECONDS
        if double or repeated:
            self._last_click = (None, 0.0)
            return self._lp_activate(item)
        self._last_click = (("lp", index), self.clock())
        return None

    def save_launch_panel(self):
        from ...config import AppError

        p = self.launch_panel
        try:
            self.provider.save_launch_settings(p.game_id, p.draft)
        except (AppError, OSError) as exc:
            p.notice = "Could not save: {}".format(str(exc)[:100])
            return None
        self.launch_panel = None
        self.reload()
        self.message = "Launch settings saved for {}".format(p.title)
        return None

    def cancel_launch_panel(self, force=False):
        p = self.launch_panel
        if p.dirty and not force:
            p.discard_warned = True
            p.notice = "Unsaved changes: press Esc again to discard them, or s to save"
            return None
        self.launch_panel = None
        self.message = "Launch settings unchanged"
        return None
