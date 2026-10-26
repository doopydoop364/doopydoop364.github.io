"""TUI launch settings: keyboard and mouse editing of arguments, environment and profiles with explicit save."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arch_games.catalog.library import Library
from arch_games.config import Config
from arch_games.runtime import gamelaunch, native, profiles
from arch_games.runtime.launchers import LauncherStore
from arch_games.runtime.pacman import PacmanService
from arch_games.storage.database import Database
from arch_games.ui.tui.data import Provider
from arch_games.ui.tui.model import SECTIONS, Model
from arch_games.ui.tui.render import Caps, render_frame
from native_fixtures import FakeSystem

PROFILE = {"os_architecture": "x86_64", "os": "linux", "wine": {"available": False}, "graphics": {"display": True}, "ram_total": 4 * 2**30}


def text(frame):
    return ["".join(t for t, _ in line) for line in frame.lines]


def type_text(model, value):
    for ch in value:
        model.handle_key(ch)


class World(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.system = FakeSystem(self.temp.name)
        ctx = self.system.active()
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.config = Config(Path(self.temp.name) / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.pacman = PacmanService(run=self.system.run)
        self.system.openttd()
        candidate = native.discover_native(self.pacman, "openttd", "OpenTTD")["candidates"][0]
        self.record = LauncherStore(self.config, self.db).register_native("pacman:openttd", "OpenTTD", "openttd", candidate, self.pacman)
        self.model = Model(Provider(self.config, self.db, Library(self.config, self.db, dict(PROFILE)).load(offline=True)))
        self.model.switch(SECTIONS.index("Installed"))
        self.assertEqual(self.model.current().key, "pacman:openttd")

    def stored(self):
        return LauncherStore(self.config, self.db).get("pacman:openttd")

    def frame(self, w=100, h=30):
        f = render_frame(self.model, w, h, Caps(False, False))
        self.model.hits = f.hits
        return f

    def open(self):
        self.model.handle_key("o")
        self.assertIsNotNone(self.model.launch_panel)


class RowAndDetailTests(World):
    def test_detail_shows_native_status_and_documents_the_shortcut(self):
        self.model.handle_key("enter")
        screen = "\n".join(text(self.frame()))
        self.assertIn("Native Linux package openttd 15.3-1: Installed and ready to launch", screen)
        self.assertIn("[o] launch settings", screen)
        self.assertIn("Executable: " + self.system.path("usr/bin/openttd"), screen)

    def test_help_and_hints_mention_launch_settings(self):
        self.assertIn("o launch settings", text(self.frame())[-1])
        self.model.handle_key("?")
        self.assertIn("launch settings: arguments, environment, profiles", "\n".join(text(self.frame())))

    def test_no_launcher_explains_instead_of_opening(self):
        LauncherStore(self.config, self.db).remove("pacman:openttd")
        self.model.provider.invalidate(reprobe=False)
        self.model.reload()
        self.model.handle_key("o")
        self.assertIsNone(self.model.launch_panel)
        self.assertIn("press x to find the executable", self.model.message)


class KeyboardEditingTests(World):
    def test_the_users_openttd_configuration_end_to_end_in_the_tui(self):
        self.open()
        screen = "\n".join(text(self.frame()))
        for needle in ("Executable: " + self.system.path("usr/bin/openttd"), "native Linux (no Wine)", "[+ add argument]", "[+ add variable NAME=VALUE]", "[ Save ]", "Command: " + self.system.path("usr/bin/openttd")):
            self.assertIn(needle, screen)
        self.model.handle_key("a")
        type_text(self.model, "-s")
        self.model.handle_key("enter")
        self.model.handle_key("a")
        type_text(self.model, "sdl")
        self.model.handle_key("enter")
        self.model.handle_key("e")
        type_text(self.model, "SDL_AUDIODRIVER=alsa")
        self.model.handle_key("enter")
        self.model.handle_key("e")
        type_text(self.model, "AUDIODEV=plughw:0,0")
        self.model.handle_key("enter")
        self.assertEqual(self.stored()["arguments"], [])  # nothing stored yet
        self.assertIn("Command: env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa {} -s sdl".format(self.system.path("usr/bin/openttd")), "\n".join(text(self.frame())))
        self.assertIn("(unsaved changes)", "\n".join(text(self.frame())))
        self.model.handle_key("s")
        self.assertIsNone(self.model.launch_panel)
        self.assertIn("saved", self.model.message)
        saved = self.stored()
        self.assertEqual((saved["arguments"], saved["environment"]), (["-s", "sdl"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}))
        self.assertEqual(saved["executable"], self.system.path("usr/bin/openttd"))  # untouched
        # and the launcher uses it
        from arch_games.runtime.launcher import Launcher

        plan = Launcher(self.config, self.db, pacman=self.pacman).plan_registered("pacman:openttd")
        self.assertEqual(plan["command_preview"], "env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa {} -s sdl".format(self.system.path("usr/bin/openttd")))

    def test_edit_and_delete_existing_items(self):
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-s", "sdl"], environment_changes={"A": "1", "B": "2"}))
        self.model.provider.invalidate(reprobe=False)
        self.open()
        items = self.model.lp_items()
        arg0 = next(i for i, it in enumerate(items) if it["kind"] == "arg" and it["index"] == 0)
        self.model.launch_panel.cursor = arg0
        self.model.handle_key("enter")  # edit the first argument
        self.assertEqual(self.model.launch_panel.input["buffer"], "-s")
        self.model.handle_key("backspace")
        self.model.handle_key("backspace")
        type_text(self.model, "-x")
        self.model.handle_key("enter")
        self.assertEqual(profiles.get(self.model.launch_panel.draft)["arguments"], ["-x", "sdl"])
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it["kind"] == "arg" and it["index"] == 1)
        self.model.handle_key("d")
        self.assertEqual(profiles.get(self.model.launch_panel.draft)["arguments"], ["-x"])
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it.get("name") == "A")
        self.model.handle_key("enter")
        self.assertEqual(self.model.launch_panel.input["buffer"], "A=1")
        for _ in range(3):
            self.model.handle_key("backspace")
        type_text(self.model, "A=9")
        self.model.handle_key("enter")
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it.get("name") == "B")
        self.model.handle_key("x")
        self.assertEqual(profiles.get(self.model.launch_panel.draft)["environment"], {"A": "9"})
        self.model.handle_key("s")
        self.assertEqual((self.stored()["arguments"], self.stored()["environment"]), (["-x"], {"A": "9"}))

    def test_invalid_input_is_explained_and_nothing_is_added(self):
        self.open()
        for entry, expected in (("A B", "NAME=VALUE"), ("1BAD=x", "Invalid environment variable name"), ("LD_PRELOAD=/x", "sensitive"), ("WINEPREFIX=/x", "managed by Arch Games"), ("PATH=/evil", "sensitive")):
            self.model.handle_key("e")
            type_text(self.model, entry)
            self.model.handle_key("enter")
            self.assertIn(expected, self.model.launch_panel.notice, entry)
            self.assertIsNotNone(self.model.launch_panel.input)  # still open so it can be corrected
            self.model.handle_key("esc")
        self.assertEqual(profiles.get(self.model.launch_panel.draft)["environment"], {})
        self.model.handle_key("a")
        self.model.handle_key("enter")
        self.assertIn("cannot be empty", self.model.launch_panel.notice)

    def test_cancel_discards_and_unsaved_changes_need_a_second_escape(self):
        self.open()
        self.model.handle_key("a")
        type_text(self.model, "-q")
        self.model.handle_key("enter")
        self.model.handle_key("esc")
        self.assertIsNotNone(self.model.launch_panel)
        self.assertIn("press Esc again", self.model.launch_panel.notice)
        self.model.handle_key("esc")
        self.assertIsNone(self.model.launch_panel)
        self.assertEqual(self.stored()["arguments"], [])
        self.open()
        self.model.handle_key("esc")  # clean panel closes at once
        self.assertIsNone(self.model.launch_panel)

    def test_profiles_create_switch_default_delete_and_save(self):
        self.open()
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it["kind"] == "new_profile")
        self.model.handle_key("enter")
        type_text(self.model, "silent")
        self.model.handle_key("enter")
        self.assertEqual(self.model.launch_panel.profile, "silent")
        self.model.handle_key("e")
        type_text(self.model, "SDL_AUDIODRIVER=dummy")
        self.model.handle_key("enter")
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it["kind"] == "set_default")
        self.model.handle_key("enter")
        self.model.handle_key("[")  # back to the default profile
        self.assertEqual(self.model.launch_panel.profile, "default")
        self.assertEqual(profiles.get(self.model.launch_panel.draft, "default")["environment"], {})
        self.model.handle_key("s")
        saved = self.stored()
        self.assertEqual((profiles.names(saved), profiles.default_name(saved), profiles.get(saved)["environment"]), (["default", "silent"], "silent", {"SDL_AUDIODRIVER": "dummy"}))
        self.open()
        self.assertEqual(self.model.launch_panel.profile, "silent")  # opens on the default profile
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it["kind"] == "delete_profile")
        self.model.handle_key("enter")
        self.assertEqual(profiles.names(self.model.launch_panel.draft), ["default"])
        self.model.handle_key("s")
        self.assertEqual(profiles.names(self.stored()), ["default"])
        self.assertEqual(profiles.default_name(self.stored()), "default")
        self.model.launch_panel = None

    def test_rediscover_runs_the_cli_command_and_status_problems_are_visible(self):
        Path(self.system.path("usr/bin/openttd")).unlink()
        self.model.provider.invalidate(reprobe=False)
        self.model.reload()
        self.model.handle_key("enter")
        self.assertIn("Launcher is missing or no longer belongs to the package", "\n".join(text(self.frame())))
        self.model.handle_key("esc")
        self.open()
        self.assertIn("launcher-invalid", self.model.launch_panel.status["status"])
        self.model.launch_panel.cursor = next(i for i, it in enumerate(self.model.lp_items()) if it["kind"] == "rediscover")
        action = self.model.handle_key("enter")
        self.assertEqual((action.kind, action.argv), ("run", ["launcher", "rediscover", "pacman:openttd"]))

    def test_failed_save_keeps_the_panel_open(self):
        self.open()
        self.model.handle_key("a")
        type_text(self.model, "-q")
        self.model.handle_key("enter")
        with patch.object(Provider, "save_launch_settings", side_effect=OSError("disk full")):
            self.model.handle_key("s")
        self.assertIsNotNone(self.model.launch_panel)
        self.assertIn("Could not save: disk full", self.model.launch_panel.notice)

    def test_the_tui_cannot_change_the_executable_or_runner(self):
        self.open()
        self.model.launch_panel.draft["executable"] = "/bin/sh"
        self.model.launch_panel.draft["runner"] = "custom"
        self.model.handle_key("s")
        self.assertEqual((self.stored()["executable"], self.stored()["runner"]), (self.system.path("usr/bin/openttd"), "native"))


class MouseTests(World):
    def item_hit(self, kind, **match):
        f = self.frame()
        items = self.model.lp_items()
        index = next(i for i, it in enumerate(items) if it["kind"] == kind and all(it.get(k) == v for k, v in match.items()))
        return next(h for h in f.hits if h.kind == "lp_item" and h.value == index)

    def test_click_buttons_add_items_and_save(self):
        self.open()
        add = self.item_hit("arg_add")
        self.model.handle_mouse("click", add.x0 + 3, add.y0)
        self.assertEqual(self.model.launch_panel.input["kind"], "arg_add")
        type_text(self.model, "-s")
        self.model.handle_key("enter")
        save = self.item_hit("save")
        self.model.handle_mouse("click", save.x0 + 3, save.y0)
        self.assertIsNone(self.model.launch_panel)
        self.assertEqual(self.stored()["arguments"], ["-s"])

    def test_single_click_selects_second_click_edits_and_wheel_scrolls(self):
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["a%d" % i for i in range(20)]))
        self.model.provider.invalidate(reprobe=False)
        self.open()
        f = self.frame(100, 16)
        first_arg = next(h for h in f.hits if h.kind == "lp_item" and self.model.lp_items()[h.value]["kind"] == "arg")
        self.model.handle_mouse("click", 5, first_arg.y0)
        self.assertIsNone(self.model.launch_panel.input)
        self.assertEqual(self.model.lp_items()[self.model.launch_panel.cursor]["kind"], "arg")
        self.model.handle_mouse("click", 5, first_arg.y0)
        self.assertEqual(self.model.launch_panel.input["kind"], "arg_edit")
        self.model.handle_key("esc")
        before = self.model.launch_panel.cursor
        self.model.handle_mouse("wheel_down", 5, 5)
        self.assertGreater(self.model.launch_panel.cursor, before)
        f = self.frame(100, 16)  # scrolled: the visible window moved and hit testing follows it
        last_visible = max((h for h in f.hits if h.kind == "lp_item"), key=lambda h: h.y0)
        self.assertGreaterEqual(last_visible.value, self.model.launch_panel.cursor)
        self.model.handle_mouse("click", 5, last_visible.y0)
        self.assertEqual(self.model.launch_panel.cursor, last_visible.value)

    def test_profile_line_click_cycles_and_cancel_button_closes(self):
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.create(r, "alt"))
        self.model.provider.invalidate(reprobe=False)
        self.open()
        prof = self.item_hit("profile")
        self.model.handle_mouse("click", prof.x0 + 3, prof.y0)
        self.assertEqual(self.model.launch_panel.profile, "alt")
        cancel = self.item_hit("cancel")
        self.model.handle_mouse("click", cancel.x0 + 2, cancel.y0)
        self.assertIsNone(self.model.launch_panel)


class RenderingTests(World):
    def test_usable_on_small_terminals_and_in_ascii(self):
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["x"] * 10, environment_changes={"V%d" % i: "1" for i in range(8)}))
        self.model.provider.invalidate(reprobe=False)
        self.open()
        for w, h in ((100, 30), (80, 24), (60, 12), (40, 8), (24, 6)):
            f = render_frame(self.model, w, h, Caps(False, False))
            lines = text(f)
            self.assertLessEqual(len(lines), h)
            for line in lines:
                self.assertLessEqual(len(line), w)
                self.assertTrue(line.isascii())
            kinds = [hh.kind for hh in f.hits if hh.kind not in {"tab", "tab_next"}]
            self.assertTrue(kinds and all(k == "lp_item" for k in kinds))
            self.assertTrue(all(hh.y0 < h for hh in f.hits))
        # the cursor line is always visible after moving to the end
        for _ in range(60):
            self.model.handle_key("down")
        f = render_frame(self.model, 60, 12, Caps(False, False))
        self.assertIn("[ Cancel ]", "\n".join(text(f)))

    def test_input_line_is_shown_while_editing(self):
        self.open()
        self.model.handle_key("a")
        type_text(self.model, "--fullscreen")
        self.assertIn("New argument: --fullscreen_", "\n".join(text(render_frame(self.model, 100, 30, Caps(False, False)))))
