"""Curses front end for the pure model/renderer. Falls back (raises Unsupported) when a real terminal is missing."""

import locale
import os
import sys

from .data import Provider
from .model import Model
from .render import Caps, render_frame

KEYS = {}


class Unsupported(Exception):
    pass


def mouse_kind(bstate, consts):
    """Translate a curses button-state bitmask into click | double | wheel_up | wheel_down | None.

    consts: mapping of ncurses constant names to values (0 when the build lacks them), so this is testable without curses."""
    get = lambda name: consts.get(name, 0)  # noqa: E731
    if bstate & get("BUTTON4_PRESSED"):
        return "wheel_up"
    if bstate & get("BUTTON5_PRESSED"):
        return "wheel_down"
    if bstate & (get("BUTTON1_DOUBLE_CLICKED") | get("BUTTON1_TRIPLE_CLICKED")):
        return "double"
    if bstate & (get("BUTTON1_CLICKED") | get("BUTTON1_PRESSED") | get("BUTTON1_RELEASED")):
        # PRESSED and RELEASED both arrive when the terminal does not synthesise clicks; the model treats a repeat as a double click,
        # so react to RELEASED/CLICKED only.
        return "click" if bstate & (get("BUTTON1_CLICKED") | get("BUTTON1_RELEASED")) else None
    return None


MOUSE_NAMES = ("BUTTON1_PRESSED", "BUTTON1_RELEASED", "BUTTON1_CLICKED", "BUTTON1_DOUBLE_CLICKED", "BUTTON1_TRIPLE_CLICKED", "BUTTON4_PRESSED", "BUTTON5_PRESSED")


def enable_mouse(curses_mod):
    """Ask for mouse events; returns the constants mapping, or None when the terminal cannot report the mouse (e.g. the Linux
    console without gpm). Keyboard use is unaffected either way."""
    try:
        consts = {name: getattr(curses_mod, name, 0) for name in MOUSE_NAMES}
        mask = 0
        for value in consts.values():
            mask |= value
        available, _ = curses_mod.mousemask(mask)
        if not available:
            return None
        return consts
    except (curses_mod.error, AttributeError):
        return None


def detect_caps(curses_mod=None, environ=None, force_ascii=False):
    env = os.environ if environ is None else environ
    enc = (locale.getpreferredencoding(False) or "").lower()
    unicode_ok = enc.startswith("utf") and env.get("TERM") != "linux" and not force_ascii and not env.get("ARCH_GAMES_ASCII")
    color = bool(curses_mod and curses_mod.has_colors()) and not env.get("NO_COLOR")
    return Caps(color=color, unicode=unicode_ok)


def key_name(curses, code):
    table = {curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_LEFT: "left", curses.KEY_RIGHT: "right",
             curses.KEY_PPAGE: "pgup", curses.KEY_NPAGE: "pgdn", curses.KEY_HOME: "home", curses.KEY_END: "end",
             curses.KEY_BACKSPACE: "backspace", 127: "backspace", 8: "backspace", 10: "enter", 13: "enter",
             curses.KEY_ENTER: "enter", 27: "esc", 9: "tab", curses.KEY_BTAB: "btab"}
    if code in table:
        return table[code]
    if 32 <= code < 0x110000 and code != 127:
        return chr(code)
    return None


def run(config, db, runner, ascii_only=False, library=None, stdout=None):
    """Interactive loop. runner(argv) executes a CLI command (the same code path the CLI uses) and returns its exit code."""
    stream = stdout or sys.stdout
    if not (sys.stdin.isatty() and stream.isatty()) or os.environ.get("TERM", "dumb") == "dumb":
        raise Unsupported("an interactive terminal is required")
    try:
        import curses
    except ImportError:
        raise Unsupported("curses is not available") from None
    locale.setlocale(locale.LC_ALL, "")
    provider = Provider(config, db, library)
    model = Model(provider)

    def main(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(-1)
        mouse = None if os.environ.get("ARCH_GAMES_NO_MOUSE") or os.environ.get("TERM") == "linux" else enable_mouse(curses)
        caps = detect_caps(curses, force_ascii=ascii_only)
        styles = {"normal": 0, "dim": getattr(curses, "A_DIM", 0), "title": curses.A_BOLD | curses.A_REVERSE, "tabs": curses.A_BOLD,
                  "sel": curses.A_REVERSE, "key": 0, "ok": 0, "warn": curses.A_BOLD, "bad": curses.A_BOLD}
        if caps.color:
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            for n, fg in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN), 1):
                curses.init_pair(n, fg, bg)
            styles.update(ok=curses.color_pair(1), warn=curses.color_pair(2) | curses.A_BOLD, bad=curses.color_pair(3) | curses.A_BOLD,
                          key=curses.color_pair(4), tabs=curses.color_pair(4) | curses.A_BOLD, title=curses.color_pair(4) | curses.A_BOLD | curses.A_REVERSE)
        while True:
            height, width = screen.getmaxyx()
            frame = render_frame(model, width - 1, height, caps, status=provider.header())
            model.hits = frame.hits  # hit testing always uses the frame that is on screen (current size and scroll)
            lines = frame.lines
            screen.erase()
            for y, spans in enumerate(lines[:height]):
                x = 0
                for text, tone in spans:
                    try:
                        screen.addstr(y, x, text, styles.get(tone, 0))
                    except curses.error:
                        pass
                    x += len(text)
            screen.refresh()
            code = screen.get_wch() if hasattr(screen, "get_wch") else screen.getch()
            action = None
            if isinstance(code, str):
                name = {"\n": "enter", "\x1b": "esc", "\t": "tab", "\x7f": "backspace", "\b": "backspace"}.get(code, code)
                action = model.handle_key(name)
            elif code == curses.KEY_RESIZE:
                continue  # the next loop iteration redraws at the new size and refreshes the hit regions
            elif mouse and code == getattr(curses, "KEY_MOUSE", None):
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                except curses.error:
                    continue
                kind = mouse_kind(bstate, mouse)
                action = model.handle_mouse(kind, mx, my) if kind else None
            else:
                name = key_name(curses, code)
                action = model.handle_key(name) if name else None
            if action is None:
                continue
            if action.kind == "quit":
                return
            if action.kind == "run":
                curses.def_prog_mode()
                curses.endwin()
                print("\n$ arch-games " + " ".join(action.argv) + "\n")
                try:
                    code = runner(action.argv)
                except SystemExit as exc:
                    code = exc.code
                print("\n[exit status {}] Press Enter to return".format(code))
                try:
                    input()
                except EOFError:
                    pass
                curses.reset_prog_mode()
                screen.clear()
                provider.invalidate()
                model.reload()

    curses.wrapper(main)
