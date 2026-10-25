from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arch_games.config import AppError, Config
from arch_games.runtime.launcher import Launcher
from arch_games.storage.database import Database
from test_detection import elf, pe


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.root = self.config.directory("installed") / "test"
        self.root.mkdir()
        self.path = self.root / "odd ; name"
        self.path.write_bytes(elf(64, 62))
        self.path.chmod(0o700)
        self.db.put("installs", "local:test", {"directory": "test", "archive_sha256": "fixture"})
        self.launcher = Launcher(self.config, self.db)

    def test_native_array_spaces(self):
        plan = self.launcher.plan("local:test", self.path.name, arguments=["--value", "$(never-run)"])
        self.assertEqual(plan["argv"], [str(self.path), "--value", "$(never-run)"])
        self.assertEqual(plan["cwd"], str(self.root))

    def test_escape_missing_and_foreign(self):
        outside = Path(self.temp.name) / "outside"
        outside.write_bytes(elf())
        (self.root / "escape").symlink_to(outside)
        for value in ["missing", "../../outside", "escape"]:
            with self.subTest(value=value), self.assertRaises(AppError):
                self.launcher.plan("local:test", value)
        self.path.write_bytes(elf(64, 183))
        with self.assertRaises(AppError):
            self.launcher.plan("local:test", self.path.name)

    def test_wine_and_dosbox_commands(self):
        self.path.write_bytes(pe())
        health = {
            "healthy": True,
            "record": {"state": "ready"},
            "architecture": "win64",
            "path": "/fixture/prefix",
        }
        self.launcher.which = lambda name: "/usr/bin/" + name
        with patch("arch_games.runtime.launcher.PrefixManager.check", return_value=health):
            plan = self.launcher.plan("local:test", self.path.name)
        self.assertEqual(plan["argv"][0], "/usr/bin/wine")
        self.assertEqual(plan["env"]["WINEPREFIX"], "/fixture/prefix")
        self.path.write_bytes(b"MZ" + bytes(62))
        self.assertEqual(self.launcher.plan("local:test", self.path.name)["method"], "dosbox")

    def test_failed_runtime_and_history(self):
        def fail(*args, **kwargs):
            raise FileNotFoundError()

        self.launcher.popen = fail
        plan = self.launcher.plan("local:test", self.path.name)
        with (
            patch("arch_games.runtime.launcher.system_profile", return_value={}),
            self.assertRaises(AppError),
        ):
            self.launcher.launch(plan)
        record = self.db.all("launches")[0]["value"]
        self.assertEqual(record["state"], "failed")
        self.assertTrue(Path(record["log"]).exists())

    def test_custom_command_and_env(self):
        plan = self.launcher.plan(
            "local:test", self.path.name, "custom", command=["/bin/echo", "{exe}"], custom_env={"LANG": "C"}
        )
        self.assertEqual(plan["argv"][1], str(self.path))
        with self.assertRaises(AppError):
            self.launcher.plan("local:test", self.path.name, custom_env={"WINEPREFIX": "/system-prefix"})


class LaunchSafetyTests(LauncherTests):
    """3.0: a graphical session is required for Windows/DOS games; game programs never run as root."""

    def wine_plan(self):
        self.path.write_bytes(pe())
        health = {"healthy": True, "record": {"state": "ready"}, "architecture": "win64", "path": "/fixture/prefix"}
        self.launcher.which = lambda name: "/usr/bin/" + name
        with patch("arch_games.runtime.launcher.PrefixManager.check", return_value=health):
            return self.launcher.plan("local:test", self.path.name)

    def test_wine_launch_without_display_explains_itself_and_starts_nothing(self):
        plan = self.wine_plan()
        plan["env"].pop("DISPLAY", None)
        plan["env"].pop("WAYLAND_DISPLAY", None)
        started = []
        self.launcher.popen = lambda *a, **k: started.append(a)
        with self.assertRaises(AppError) as ctx:
            self.launcher.launch(plan)
        self.assertIn("X11 or Wayland", str(ctx.exception))
        self.assertEqual(started, [])
        self.assertEqual(self.db.all("launches"), [])
        plan["env"]["WAYLAND_DISPLAY"] = "wayland-0"
        class P:
            def wait(self, timeout=None):
                return 0
        self.launcher.popen = lambda *a, **k: P()
        self.assertEqual(self.launcher.launch(plan)["exit_code"], 0)

    def test_silent_installer_may_run_headless_native_never_needs_display(self):
        plan = self.wine_plan()
        plan.update(purpose="installer", interactive=False)
        plan["env"].pop("DISPLAY", None)
        plan["env"].pop("WAYLAND_DISPLAY", None)
        class P:
            def wait(self, timeout=None):
                return 0
        self.launcher.popen = lambda *a, **k: P()
        self.assertEqual(self.launcher.launch(plan)["purpose"], "installer")
        plan["interactive"] = True
        with self.assertRaises(AppError):
            self.launcher.launch(plan)

    def test_refuses_to_run_as_root(self):
        with patch("arch_games.runtime.launcher.os.geteuid", return_value=0), self.assertRaises(AppError) as ctx:
            self.wine_plan()
        self.assertIn("root", str(ctx.exception))
