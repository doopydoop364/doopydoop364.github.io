"""Curses front end for the pure model/renderer. Falls back (raises Unsupported) when a real terminal is missing."""

import locale
import os
import sys

from .data import Provider
from .model import Model
from .render import Caps, render

KEYS = {}


class Unsupported(Exception):
    pass


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
            lines = render(model, width - 1, height, caps, status=provider.header())
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
            if isinstance(code, str):
                name = {"\n": "enter", "\x1b": "esc", "\t": "tab", "\x7f": "backspace", "\b": "backspace"}.get(code, code)
            else:
                if code == curses.KEY_RESIZE:
                    continue
                name = key_name(curses, code)
            if not name:
                continue
            action = model.handle_key(name)
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
