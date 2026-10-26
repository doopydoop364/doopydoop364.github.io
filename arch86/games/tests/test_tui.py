"""TUI: pure model/renderer tests plus a real pseudo-terminal run (curses over a pty, no graphical session)."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import os
from pathlib import Path
import pty
import select
import sys
import tempfile
import time
import unittest

from arch_games.config import Config
from arch_games.models import Game
from arch_games.storage.database import Database
from arch_games.ui.tui.model import SECTIONS, Model, Row
from arch_games.ui.tui.render import Caps, render


class FakeProvider:
    def __init__(self, n=30):
        self.data = {s: [] for s in SECTIONS}
        self.data["Library"] = [Row("g:%d" % i, "Game %02d" % i, "Promising", "ok", [("Source: test", "normal")], {"l": ("launch", ["launch", "g:%d" % i]), "i": ("install", ["install", "g:%d" % i])}) for i in range(n)]
        if n > 3:
            self.data["Library"][3].label = "Undertale"
        self.invalidated = 0

    def rows(self, section, search, show_all):
        rows = self.data[section]
        return [r for r in rows if search.casefold() in r.label.casefold()]

    def invalidate(self):
        self.invalidated += 1

    def empty_text(self, section):
        return "Nothing here"

    def header(self):
        return "x86_64"


def text(lines):
    return ["".join(t for t, _ in line) for line in lines]


class ModelTests(unittest.TestCase):
    def test_navigation_search_actions(self):
        m = Model(FakeProvider())
        for key in ["down", "j", "j"]:
            m.handle_key(key)
        self.assertEqual(m.current().label, "Undertale")
        self.assertEqual(m.handle_key("l").argv, ["launch", "g:3"])
        self.assertIsNone(m.handle_key("z"))
        self.assertIn("No action", m.message)
        for key in ["/", "u", "n", "d", "enter"]:
            m.handle_key(key)
        self.assertEqual([r.label for r in m.rows], ["Undertale"])
        m.handle_key("enter")
        self.assertTrue(m.detail_open)
        m.handle_key("esc")
        self.assertFalse(m.detail_open)
        m.handle_key("esc")
        self.assertEqual(m.search, "")
        self.assertEqual(len(m.rows), 30)

    def test_search_mode_swallows_action_keys_and_esc_clears(self):
        m = Model(FakeProvider())
        m.handle_key("/")
        self.assertIsNone(m.handle_key("q"))  # typing q, not quitting
        self.assertEqual(m.search, "q")
        m.handle_key("esc")
        self.assertEqual((m.search, m.searching), ("", False))
        self.assertEqual(m.handle_key("q").kind, "quit")

    def test_sections_wrap_and_digits(self):
        m = Model(FakeProvider())
        m.handle_key("btab")
        self.assertEqual(m.name, "Diagnostics")
        m.handle_key("tab")
        self.assertEqual(m.name, "Library")
        m.handle_key("6")
        self.assertEqual(m.name, "Wine")
        m.handle_key("0")
        self.assertEqual(m.name, "Wine")

    def test_help_and_refresh(self):
        p = FakeProvider()
        m = Model(p)
        m.handle_key("?")
        self.assertTrue(m.help_open)
        m.handle_key("x")
        self.assertFalse(m.help_open)
        m.handle_key("r")
        self.assertEqual(p.invalidated, 1)

    def test_cursor_bounds(self):
        m = Model(FakeProvider(3))
        for _ in range(10):
            m.handle_key("down")
        self.assertEqual(m.cursor, 2)
        m.handle_key("pgup")
        self.assertEqual(m.cursor, 0)
        empty = Model(FakeProvider(0))
        empty.handle_key("down")
        empty.handle_key("enter")
        self.assertFalse(empty.detail_open)


class RenderTests(unittest.TestCase):
    def test_every_size_fits_unicode_and_ascii(self):
        m = Model(FakeProvider())
        m.handle_key("j")
        for w in [1, 5, 20, 30, 40, 60, 80, 100, 160]:
            for h in [1, 3, 6, 10, 24, 50]:
                for caps in (Caps(True, True), Caps(False, False)):
                    for mode in ("list", "detail", "help"):
                        m.detail_open, m.help_open = mode == "detail", mode == "help"
                        lines = render(m, w, h, caps)
                        self.assertLessEqual(len(lines), max(h, 1))
                        for line in text(lines):
                            self.assertLessEqual(len(line), w if w >= 1 else 1, (w, h, mode, line))
                            if not caps.unicode:
                                self.assertTrue(line.isascii(), line)

    def test_wide_layout_has_preview_pane_and_hints(self):
        m = Model(FakeProvider())
        m.handle_key("j")
        out = text(render(m, 120, 30, Caps(True, True)))
        self.assertIn("ARCH GAMES 3.0", out[0])
        self.assertTrue(any("Source: test" in line for line in out))
        self.assertIn("l launch", out[-1])
        self.assertIn("q quit", out[-1])
        self.assertTrue(any("[Library]" in line for line in out))

    def test_small_terminal_message(self):
        out = text(render(Model(FakeProvider()), 10, 4))
        self.assertIn("Too small", out[0])

    def test_hostile_text_is_neutralised(self):
        p = FakeProvider(1)
        p.data["Library"][0].label = "Evil\x1b[2Jgame‮"
        out = "".join(text(render(Model(p), 80, 24)))
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)


class ProviderIntegrationTests(unittest.TestCase):
    def test_real_provider_sections(self):
        from arch_games.ui.tui.data import Provider
        from arch_games.runtime.launchers import LauncherStore  # noqa: F401

        with tempfile.TemporaryDirectory() as d:
            config = Config(Path(d) / "s")
            db = Database(config.data_dir / "state.db")
            self.addCleanup(db.close)
            db.put("installs", "local:one", {"directory": "x", "game": Game("local:one", "One", "local").to_dict(), "archive_sha256": "a"})
            db.put("favorites", "local:one", {})
            db.put("aliases", "o", {"alias": "o", "game_id": "local:one"})
            db.put("downloads", "d1", {"id": "d1", "title": "T", "game_id": "gamevault:1", "state": "failed", "received": 5, "expected_size": 10, "error": "net"})
            provider = Provider(config, db)
            model = Model(provider)
            for index in range(len(SECTIONS)):
                model.switch(index)
            model.switch(0)
            self.assertEqual(model.rows[0].key, "local:one")  # favorites sort first
            self.assertTrue(model.rows[0].favorite)
            self.assertIn("Aliases: o", [t for t, _ in model.rows[0].detail])
            model.switch(1)
            self.assertEqual([r.key for r in model.rows], ["local:one"])
            model.switch(3)
            self.assertIn("t", model.rows[0].actions)
            self.assertEqual(model.rows[0].actions["t"][1], ["download", "retry", "d1"])


@unittest.skipUnless(os.name == "posix", "needs a pty")
class PtyTests(unittest.TestCase):
    def run_session(self, keys, env_extra=None, size=(24, 100), setup=None):
        import fcntl
        import struct
        import termios

        with tempfile.TemporaryDirectory() as d:
            if setup:
                setup(d)
            pid, fd = pty.fork()
            if pid == 0:
                env = {**os.environ, "TERM": "xterm", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", **(env_extra or {})}
                env.pop("DISPLAY", None)
                env.pop("WAYLAND_DISPLAY", None)
                root = str(Path(__file__).resolve().parents[1])
                os.execvpe(sys.executable, [sys.executable, "-m", "arch_games", "--state-dir", d, "menu", "--offline"], {**env, "PYTHONPATH": root})
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
            output = b""

            def pump(seconds):
                nonlocal output
                end = time.time() + seconds
                while time.time() < end:
                    r, _, _ = select.select([fd], [], [], 0.1)
                    if r:
                        try:
                            chunk = os.read(fd, 65536)
                        except OSError:
                            return False
                        if not chunk:
                            return False
                        output += chunk
                return True

            pump(1.5)
            for key in keys:
                os.write(fd, key)
                if not pump(0.5):
                    break
            for _ in range(20):
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    return output.decode("utf-8", "replace"), status
                time.sleep(0.1)
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            return output.decode("utf-8", "replace"), None

    def test_starts_navigates_and_quits_without_graphical_session(self):
        out, status = self.run_session([b"\t", b"\t", b"?", b"x", b"q"])
        self.assertIn("ARCH GAMES 3.0", out)
        self.assertIn("Keyboard and mouse help", out)
        self.assertEqual(status, 0, out[-400:])

    def test_mouse_click_changes_section_in_a_real_curses_session_and_is_off_on_the_linux_console(self):
        def sgr(x, y, release):  # xterm SGR mouse report, left button, 1-based cell
            return "\x1b[<0;{};{}{}".format(x, y, "m" if release else "M").encode()

        out, status = self.run_session([sgr(15, 2, False), sgr(15, 2, True), b"q"])
        self.assertIn("1000h", out)  # the terminal was asked to report the mouse
        self.assertIn("Nothing is installed or registered", out)  # the click on the "Installed" tab opened that section
        self.assertEqual(status, 0)
        out, status = self.run_session([b"q"], {"ARCH_GAMES_NO_MOUSE": "1"})
        self.assertNotIn("1000h", out)
        self.assertEqual(status, 0)
        out, status = self.run_session([b"q"], {"TERM": "linux", "LANG": "C"})
        self.assertNotIn("1000h", out)  # basic Linux console / KMSCON path: keyboard only
        self.assertEqual(status, 0)

    def test_launch_settings_panel_opens_in_a_real_curses_session(self):
        def seed(d):
            from arch_games.config import Config
            from arch_games.storage.database import Database

            config = Config(d)
            db = Database(config.data_dir / "state.db")
            program = Path(d) / "mygame"
            program.write_bytes(b"\x7fELF" + bytes(100))
            db.put("launchers", "local:mygame", {"game_id": "local:mygame", "title": "My Game", "executable": str(program), "runner": "native", "confirmed": True,
                                                 "arguments": ["-s", "sdl"], "environment": {"SDL_AUDIODRIVER": "alsa"}})
            db.close()

        out, status = self.run_session([b"2", b"o", b"a", b"-q", b"\r", b"\x1b", b"\x1b", b"q"], setup=seed)
        self.assertIn("Launch settings: My Game", out)
        self.assertIn("SDL_AUDIODRIVER=alsa", out)
        self.assertIn("New argument", out)
        self.assertEqual(status, 0)

    def test_ascii_fallback_on_linux_console_term(self):
        out, status = self.run_session([b"q"], {"TERM": "linux", "LANG": "C"})
        self.assertIn("ARCH GAMES 3.0", out)
        self.assertEqual(status, 0)
        self.assertNotIn("│", out)

    def test_tiny_terminal_does_not_crash(self):
        out, status = self.run_session([b"j", b"q"], size=(5, 15))
        self.assertEqual(status, 0, out[-300:])
