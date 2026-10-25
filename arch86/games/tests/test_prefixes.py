from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from arch_games.config import AppError, Config
from arch_games.runtime.prefixes import PrefixManager
from arch_games.storage.database import Database


class PrefixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)

    def runner(self, args, **kwargs):
        if "--version" in args:
            return SimpleNamespace(returncode=0, stdout="wine-fixture\n")
        if args[0].endswith("wineserver"):
            return SimpleNamespace(returncode=0)
        path = Path(kwargs["env"]["WINEPREFIX"])
        (path / "system.reg").write_text("WINE REGISTRY Version 2\n#arch=" + kwargs["env"]["WINEARCH"] + "\n")
        (path / "user.reg").write_text("WINE REGISTRY Version 2\n#arch=" + kwargs["env"]["WINEARCH"] + "\n")
        (path / "drive_c").mkdir()
        return SimpleNamespace(returncode=0)

    def test_create_health_backup_remove(self):
        manager = PrefixManager(self.config, self.db, self.runner, lambda name: "/usr/bin/" + name)
        manager.create("gamevault:1", "win32")
        self.assertTrue(manager.check("gamevault:1")["healthy"])
        self.assertEqual(manager.check("gamevault:1")["architecture"], "win32")
        self.assertTrue(Path(manager.backup("gamevault:1")["backup"]).is_dir())
        self.assertTrue(Path(manager.remove("gamevault:1")["backup"]).is_dir())
        self.assertFalse(self.db.all("prefixes"))

    def test_arch_and_missing_runtime(self):
        manager = PrefixManager(self.config, self.db, which=lambda name: None)
        with self.assertRaises(AppError):
            manager.create("id")
        with (
            patch("arch_games.runtime.prefixes.architecture", return_value="i686"),
            self.assertRaises(AppError),
        ):
            manager.create("id", "win64")

    def test_failed_wine_retains_diagnostic_state(self):
        def fail(args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="")

        manager = PrefixManager(self.config, self.db, fail, lambda name: "/usr/bin/" + name)
        with self.assertRaises(AppError):
            manager.create("id")
        self.assertEqual(self.db.get("prefixes", "id")["state"], "failed")
