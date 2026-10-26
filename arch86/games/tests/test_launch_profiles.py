"""Launch profiles: validation, pure edits, persistence, effective configuration and safe native launching."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arch_games.config import AppError, Config
from arch_games.runtime import gamelaunch, native, profiles
from arch_games.runtime.launcher import Launcher
from arch_games.runtime.launchers import LauncherStore
from arch_games.runtime.pacman import PacmanService
from arch_games.storage.database import Database
from native_fixtures import FakeSystem

BASE = {"game_id": "g", "executable": "/usr/bin/g", "runner": "native", "arguments": ["-a"], "environment": {"A": "1"}, "working_directory": None}


class ProfileModelTests(unittest.TestCase):
    def test_env_validation_matrix(self):
        ok = {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0", "MESA_GL_VERSION_OVERRIDE": "4.5", "_X": "", "a1": "v w"}
        self.assertEqual(profiles.validate_env(ok), ok)
        bad_names = ["", "1A", "A-B", "A B", "A=B", "é", "A" * 200, None, 5, "A\0"]
        for name in bad_names:
            with self.subTest(name=name), self.assertRaises(AppError):
                profiles.validate_env({name: "x"})
        for value in ["a\0b", "line\nbreak", "cr\rx", "x" * 9000, None, 5]:
            with self.subTest(value=value), self.assertRaises(AppError):
                profiles.validate_env({"OK": value})
        for reserved in ("WINEPREFIX", "WINEARCH"):
            with self.assertRaises(AppError):
                profiles.validate_env({reserved: "/x"}, allow_sensitive=True)  # reserved even when sensitive names are allowed
        with self.assertRaises(AppError):
            profiles.validate_env({"V%d" % i: "x" for i in range(100)})

    def test_sensitive_variables_need_an_explicit_override(self):
        for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "PATH", "HOME", "SHELL", "IFS", "BASH_ENV", "PYTHONPATH", "XDG_RUNTIME_DIR", "SUDO_USER", "LD_ANYTHING"):
            with self.subTest(name=name):
                with self.assertRaises(AppError) as ctx:
                    profiles.validate_env({name: "x"})
                self.assertIn("sensitive", str(ctx.exception))
                self.assertEqual(profiles.validate_env({name: "x"}, allow_sensitive=True), {name: "x"})
        self.assertIn("sensitive", profiles.POLICY_TEXT.lower())
        self.assertIn("WINEPREFIX", profiles.POLICY_TEXT)

    def test_argument_validation(self):
        self.assertEqual(profiles.validate_args(["-s", "sdl", "--name=a b", "$(touch x)", "`id`", "; rm -rf /"]), ["-s", "sdl", "--name=a b", "$(touch x)", "`id`", "; rm -rf /"])
        for bad in (["a\0b"], ["a\nb"], [5], ["x" * 5000], ["a"] * 100, "not-a-list"):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                profiles.validate_args(bad)

    def test_edits_are_pure_and_default_profile_maps_to_top_level_fields(self):
        original = copy.deepcopy(BASE)
        edited = profiles.edit(BASE, None, arguments=["-s", "sdl"], environment_changes={"B": "2"}, remove_env=["A"])
        self.assertEqual(BASE, original)  # the input is never mutated
        cfg = profiles.get(edited)
        self.assertEqual((cfg["profile"], cfg["arguments"], cfg["environment"]), ("default", ["-s", "sdl"], {"B": "2"}))
        self.assertEqual((edited["arguments"], edited["environment"]), (["-s", "sdl"], {"B": "2"}))  # historical top-level layout kept

    def test_profile_lifecycle(self):
        record = profiles.create(BASE, "quiet")
        self.assertEqual(profiles.names(record), ["default", "quiet"])
        self.assertEqual(profiles.get(record, "quiet")["arguments"], ["-a"])  # copied from the default profile
        record = profiles.edit(record, "quiet", environment_changes={"SDL_AUDIODRIVER": "dummy"})
        self.assertEqual(profiles.get(record, "quiet")["environment"], {"A": "1", "SDL_AUDIODRIVER": "dummy"})
        self.assertEqual(profiles.get(record)["environment"], {"A": "1"})  # the default profile is untouched
        record = profiles.set_default(record, "quiet")
        self.assertEqual(profiles.get(record)["profile"], "quiet")
        self.assertEqual(profiles.get(record, "default")["profile"], "default")
        record = profiles.reset(record, "quiet")
        self.assertEqual((profiles.get(record, "quiet")["arguments"], profiles.get(record, "quiet")["environment"]), ([], {}))
        record = profiles.delete(record, "quiet")
        self.assertEqual((profiles.names(record), profiles.default_name(record)), (["default"], "default"))
        for bad in (lambda: profiles.delete(record, "default"), lambda: profiles.create(record, "default"), lambda: profiles.create(record, "bad/name"),
                    lambda: profiles.create(record, ""), lambda: profiles.set_default(record, "nope"), lambda: profiles.get(record, "nope"),
                    lambda: profiles.edit(record, "nope", arguments=[])):
            with self.assertRaises(AppError):
                bad()
        with self.assertRaises(AppError):
            record = profiles.create(record, "a")
            record = profiles.create(record, "a")

    def test_command_preview_quotes_for_display_only(self):
        text = profiles.command_preview("/usr/bin/openttd", ["-s", "sdl", "a b", "$(x)"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}, "native")
        self.assertEqual(text, "env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa /usr/bin/openttd -s sdl 'a b' '$(x)'")
        self.assertEqual(profiles.command_preview("/g.exe", [], {}, "wine"), "wine /g.exe")


class Base(unittest.TestCase):
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
        self.started = []
        self.exit_code = 0

        class Proc:
            def __init__(inner, code):
                inner.code = code

            def wait(inner, timeout=None):
                return inner.code

        self.launcher = Launcher(self.config, self.db, which=self.no_wine, popen=lambda argv, **kw: (self.started.append((argv, kw)), Proc(self.exit_code))[1], pacman=self.pacman)

    def no_wine(self, name):
        raise AssertionError("native games must not look for Wine ({})".format(name))

    def register(self, script=False):
        exe = self.system.openttd(script=script)
        candidate = native.discover_native(self.pacman, "openttd", "OpenTTD")["candidates"][0]
        return LauncherStore(self.config, self.db).register_native("pacman:openttd", "OpenTTD", "openttd", candidate, self.pacman), exe


class NativeLaunchTests(Base):
    def test_plan_uses_saved_arguments_environment_and_never_wine(self):
        record, exe = self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-s", "sdl"], environment_changes={"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}))
        with patch.dict(os.environ, {"DISPLAY": ":0"}):
            plan = self.launcher.plan_registered("pacman:openttd")
        self.assertEqual(plan["argv"], [exe, "-s", "sdl"])  # an argument array: no shell, no `env` wrapper
        self.assertEqual((plan["method"], plan["runtime"], plan["profile"]), ("native", "native Linux (no Wine)", "default"))
        self.assertEqual(plan["env"]["SDL_AUDIODRIVER"], "alsa")
        self.assertEqual(plan["env"]["AUDIODEV"], "plughw:0,0")
        self.assertNotIn("WINEPREFIX", plan["env"])
        self.assertEqual(plan["cwd"], str(Path.home()))
        self.assertEqual(plan["custom_environment"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"})
        self.assertEqual(plan["package"], {"name": "openttd", "version": "15.3-1"})
        self.assertEqual(plan["command_preview"], "env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa {} -s sdl".format(exe))
        self.assertEqual(plan["warnings"], [])
        self.assertEqual(self.started, [])  # planning never starts anything

    def test_extra_cli_arguments_and_env_are_appended_on_top(self):
        self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-s", "sdl"], environment_changes={"A": "1"}))
        plan = self.launcher.plan_registered("pacman:openttd", extra_arguments=["-g"], extra_env={"A": "2", "B": "3"})
        self.assertEqual(plan["argv"][1:], ["-s", "sdl", "-g"])
        self.assertEqual(plan["custom_environment"], {"A": "2", "B": "3"})

    def test_profiles_select_different_configurations(self):
        self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, environment_changes={"SDL_AUDIODRIVER": "alsa"}))
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.create(r, "silent"))
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, "silent", arguments=["-m", "null"], environment_changes={"SDL_AUDIODRIVER": "dummy"}))
        self.assertEqual(self.launcher.plan_registered("pacman:openttd")["custom_environment"], {"SDL_AUDIODRIVER": "alsa"})
        silent = self.launcher.plan_registered("pacman:openttd", profile="silent")
        self.assertEqual((silent["profile"], silent["argv"][1:], silent["custom_environment"]), ("silent", ["-m", "null"], {"SDL_AUDIODRIVER": "dummy"}))
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.set_default(r, "silent"))
        self.assertEqual(self.launcher.plan_registered("pacman:openttd")["profile"], "silent")
        with self.assertRaises(AppError):
            self.launcher.plan_registered("pacman:openttd", profile="missing")

    def test_no_shell_injection_through_arguments_or_values(self):
        self.register()
        marker = Path(self.temp.name) / "pwned"
        nasty = ["$(touch {})".format(marker), "`touch {}`".format(marker), "; touch {}".format(marker), "&& touch {}".format(marker), "a b", "*"]
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=nasty, environment_changes={"X": "$(touch {})".format(marker)}))
        plan = self.launcher.plan_registered("pacman:openttd")
        self.assertEqual(plan["argv"][1:], nasty)  # literal, one argv entry each
        self.assertEqual(plan["env"]["X"], "$(touch {})".format(marker))
        self.launcher.launch(plan)
        self.assertFalse(marker.exists())
        argv, kwargs = self.started[0]
        self.assertIsInstance(argv, list)
        self.assertFalse(kwargs.get("shell", False))

    def test_wrapper_scripts_launch_natively(self):
        record, exe = self.register(script=True)
        self.assertEqual(record["binary"]["format"], "script")
        plan = self.launcher.plan_registered("pacman:openttd")
        self.assertEqual((plan["method"], plan["argv"][0]), ("native", exe))

    def test_stale_or_removed_launchers_are_refused_with_the_next_step(self):
        record, exe = self.register()
        Path(exe).unlink()
        with self.assertRaises(AppError) as ctx:
            self.launcher.plan_registered("pacman:openttd")
        self.assertIn("launcher rediscover pacman:openttd", str(ctx.exception))
        self.system.remove_package("openttd")
        with self.assertRaises(AppError) as ctx:
            self.launcher.plan_registered("pacman:openttd")
        self.assertIn("no longer installed", str(ctx.exception))
        self.assertEqual(self.started, [])

    def test_ownership_is_verified_at_launch_time(self):
        record, exe = self.register()
        self.system.owner_overrides[exe] = "impostor"
        with self.assertRaises(AppError):
            self.launcher.plan_registered("pacman:openttd")

    def test_a_tampered_record_cannot_point_the_launcher_at_an_unowned_program(self):
        record, exe = self.register()
        stray = self.system.program("stray")
        tampered = dict(record, executable=stray, resolved_executable=stray)
        self.db.put("launchers", "pacman:openttd", tampered)
        with self.assertRaises(AppError):
            self.launcher.plan_registered("pacman:openttd")
        outside = Path(self.temp.name) / "evil"
        outside.write_bytes(b"\x7fELF" + bytes(100))
        outside.chmod(0o755)
        self.db.put("launchers", "pacman:openttd", dict(record, executable=str(outside)))
        with self.assertRaises(AppError):
            self.launcher.plan_registered("pacman:openttd")
        self.assertEqual(self.started, [])

    def test_refuses_root(self):
        self.register()
        with patch("arch_games.runtime.launcher.os.geteuid", return_value=0), self.assertRaises(AppError):
            self.launcher.plan_registered("pacman:openttd")

    def test_no_display_is_a_warning_for_native_games_not_a_block(self):
        self.register()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            plan = self.launcher.plan_registered("pacman:openttd")
        self.assertTrue(any("X11/Wayland" in w for w in plan["warnings"]))
        self.assertEqual(self.launcher.launch(plan)["exit_code"], 0)

    def test_launch_history_and_compatibility_evidence(self):
        from arch_games.compatibility import intelligence

        self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-s", "sdl"], environment_changes={"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}))
        record = self.launcher.launch(self.launcher.plan_registered("pacman:openttd"))
        self.assertEqual((record["state"], record["exit_code"], record["profile"], record["arguments"]), ("exited", 0, "default", ["-s", "sdl"]))
        self.assertEqual(record["environment_overrides"]["AUDIODEV"], "plughw:0,0")
        self.assertEqual(self.db.get("launches", record["id"])["package"], {"name": "openttd", "version": "15.3-1"})
        machine = intelligence.machine_identity(self.config)["id"]
        compat = intelligence.get(self.db, machine, "pacman:openttd")
        self.assertEqual((compat["launch_status"], compat["gameplay_status"], compat["title"]), ("exited_ok", "unverified", "OpenTTD"))
        self.assertEqual(compat["launch_profile"]["environment"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"})
        self.assertEqual(compat["launch_profile"]["arguments"], ["-s", "sdl"])
        classification, _ = intelligence.classify({"status": "Experimental", "evidence": [], "uncertainties": []}, compat, measured=True)
        self.assertEqual(classification, "Likely compatible")  # it started: still not 'Verified working'
        shared = intelligence.shareable(compat)
        self.assertEqual(shared["launch_profile"]["environment"], ["AUDIODEV", "SDL_AUDIODRIVER"])  # values never leave the machine
        self.exit_code = 1
        failed = self.launcher.launch(self.launcher.plan_registered("pacman:openttd"))
        self.assertEqual(failed["exit_code"], 1)
        self.assertEqual(intelligence.get(self.db, machine, "pacman:openttd")["launch_status"], "exited_error")


class ValidateAndSettingsTests(Base):
    def test_validate_reports_each_check_without_launching(self):
        self.assertFalse(gamelaunch.validate(self.config, self.db, "pacman:openttd", pacman=self.pacman, launcher=self.launcher)["ok"])
        self.register()
        report = gamelaunch.validate(self.config, self.db, "pacman:openttd", pacman=self.pacman, launcher=self.launcher)
        self.assertTrue(report["ok"], report)
        self.assertIn("openttd", report["preview"])
        self.assertEqual(self.started, [])
        bad = gamelaunch.validate(self.config, self.db, "pacman:openttd", profile="missing", pacman=self.pacman, launcher=self.launcher)
        self.assertFalse(bad["ok"])
        Path(self.system.path("usr/bin/openttd")).unlink()
        stale = gamelaunch.validate(self.config, self.db, "pacman:openttd", pacman=self.pacman, launcher=self.launcher)
        self.assertFalse(stale["ok"])
        self.assertIsNone(stale["preview"])

    def test_settings_persist_across_reloads_and_survive_a_second_database_connection(self):
        self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-s", "sdl"], environment_changes={"AUDIODEV": "plughw:0,0"}))
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.create(r, "alt"))
        self.db.close()
        fresh = Database(self.config.data_dir / "state.db")
        self.addCleanup(fresh.close)
        record = LauncherStore(self.config, fresh).get("pacman:openttd")
        self.assertEqual(profiles.get(record)["arguments"], ["-s", "sdl"])
        self.assertEqual(profiles.names(record), ["default", "alt"])
        self.db = Database(self.config.data_dir / "state.db")  # keep tearDown's close() valid

    def test_old_launcher_records_without_new_fields_still_work(self):
        legacy = {"game_id": "local:x", "title": "X", "executable": "/x", "runner": "native", "arguments": ["-a"], "environment": {"E": "1"}, "working_directory": "/tmp", "confirmed": True}
        self.assertEqual(profiles.names(legacy), ["default"])
        cfg = profiles.get(legacy)
        self.assertEqual((cfg["arguments"], cfg["environment"], cfg["working_directory"]), (["-a"], {"E": "1"}, "/tmp"))
        updated = profiles.edit(legacy, None, environment_changes={"F": "2"})
        self.assertEqual(updated["environment"], {"E": "1", "F": "2"})


class GrandfatheredSensitiveVariableTests(unittest.TestCase):
    """A sensitive variable saved earlier through the CLI (--allow-sensitive) must not lock the TUI/web out of saving other changes."""

    BASE = {"game_id": "g", "executable": "/usr/bin/g", "runner": "native", "arguments": [], "environment": {"LD_LIBRARY_PATH": "/opt/x", "A": "1"}}

    def test_unchanged_sensitive_variable_survives_a_form_save_but_cannot_be_added_or_changed(self):
        saved = profiles.replace(self.BASE, None, ["-q"], {"LD_LIBRARY_PATH": "/opt/x", "A": "2"})
        self.assertEqual(saved["environment"], {"LD_LIBRARY_PATH": "/opt/x", "A": "2"})
        with self.assertRaises(AppError):
            profiles.replace(self.BASE, None, [], {"LD_LIBRARY_PATH": "/opt/other"})  # changed
        with self.assertRaises(AppError):
            profiles.replace(self.BASE, None, [], {"LD_LIBRARY_PATH": "/opt/x", "LD_PRELOAD": "/evil.so"})  # added
        with self.assertRaises(AppError):
            profiles.replace(self.BASE, None, [], {"WINEPREFIX": "/x"})
        draft = dict(self.BASE, arguments=["-z"])
        applied = profiles.apply_user_settings(self.BASE, draft)
        self.assertEqual(applied["environment"], self.BASE["environment"])
        self.assertEqual(applied["arguments"], ["-z"])
        with self.assertRaises(AppError):
            profiles.apply_user_settings(self.BASE, dict(self.BASE, environment={"PATH": "/evil"}))
