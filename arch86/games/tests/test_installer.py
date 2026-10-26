import no_probe  # noqa: F401  (must come first: disables real system probes)
from pathlib import Path
import tempfile
import unittest
import zipfile
from arch_games.config import AppError, Config
from arch_games.models import Game
from arch_games.runtime.installer import Installer, MANIFEST
from arch_games.storage.database import Database
from test_detection import pe


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.installer = Installer(self.config, self.db)
        self.archive = Path(self.temp.name) / "game.zip"
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("Game/Game.exe", pe())
        self.game = Game("local:test", "Game", "local")

    def test_install_reinstall_uninstall(self):
        manifest = self.installer.install(self.game, self.archive)
        self.assertTrue((self.installer.root / manifest["directory"] / MANIFEST).is_file())
        self.assertEqual(len(manifest["executables"]), 1)
        self.assertTrue(self.archive.is_file())
        with self.assertRaises(AppError):
            self.installer.install(self.game, self.archive)
        manifest = self.installer.install(self.game, self.archive, reinstall=True)
        self.assertTrue((self.installer.backups / manifest["previous_backup"]).is_dir())
        result = self.installer.uninstall(self.game.id)
        self.assertTrue(Path(result["backup"]).is_dir())
        self.assertIsNone(self.db.get("installs", self.game.id))

    def test_failed_extraction_no_partial_install(self):
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("../escape", "bad")
        with self.assertRaises(AppError):
            self.installer.install(self.game, self.archive)
        self.assertFalse(self.db.all("installs"))
        self.assertFalse(list(self.installer.root.glob(".staging-*")))

    def test_recover_interrupted_commit(self):
        manifest = self.installer.install(self.game, self.archive)
        op = {
            "transaction": manifest["transaction"],
            "game_id": self.game.id,
            "destination": manifest["directory"],
            "stage": ".staging-crash",
            "backup": "crash-backup",
        }
        stage = self.installer.root / op["stage"]
        stage.mkdir()
        self.db.delete("installs", self.game.id)
        self.db.put("install_transactions", op["transaction"], op)
        self.installer.recover()
        self.assertIsNotNone(self.db.get("installs", self.game.id))
        self.assertFalse(stage.exists())

    def test_removal_journal_recovery(self):
        manifest = self.installer.install(self.game, self.archive)
        operation = {
            "kind": "remove",
            "game_id": self.game.id,
            "destination": manifest["directory"],
            "backup": "removed-crash",
            "purge": False,
        }
        self.db.put("install_transactions", "crash", operation)
        self.installer.recover()
        self.assertIsNone(self.db.get("installs", self.game.id))
        self.assertTrue((self.installer.backups / "removed-crash").is_dir())
