import no_probe  # noqa: F401  (must come first: disables real system probes)
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.downloads.manager import DownloadManager, positive_size
from arch_games.models import Game
from arch_games.runtime.launcher import Launcher
from arch_games.storage.database import Database
from test_detection import elf


class RegressionTests(unittest.TestCase):
    def test_unknown_file_report_and_damaged_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "unknown.bin"
            path.write_bytes(b"Unrecognized bytes, not an archive")
            with (
                patch("arch_games.platform.system_profile", return_value={}),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(main(["--state-dir", temp, "analyze", str(path)]), 0)
            self.assertIn("Unknown", output.getvalue())
            with (
                patch("arch_games.cli.Database", side_effect=sqlite3.DatabaseError("damaged")),
                redirect_stderr(io.StringIO()) as error,
            ):
                self.assertEqual(main(["--state-dir", temp, "info"]), 1)
            self.assertIn("database", error.getvalue())

    def test_fully_received_partial_recovers_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Config(temp)
            db = Database(config.data_dir / "state.db")
            try:
                manager = DownloadManager(config, db, client=object())
                game = Game("gamevault:1", "Fixture", "gamevault", metadata={"remote": {"size": "6"}})
                job = manager.add(game)
                manager.path(job, True).write_bytes(b"abcdef")
                manager.run()
                self.assertEqual(manager.get(job["id"])["state"], "completed")
            finally:
                db.close()

    def test_interactive_attaches_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Config(temp)
            db = Database(config.data_dir / "state.db")
            try:
                root = config.directory("installed") / "fixture"
                root.mkdir()
                path = root / "game"
                path.write_bytes(elf(64, 62))
                path.chmod(0o700)
                db.put("installs", "local:fixture", {"directory": "fixture"})
                calls = []

                def popen(*args, **kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(wait=lambda: 0)

                launcher = Launcher(config, db, popen=popen)
                plan = launcher.plan("local:fixture", "game")
                plan["interactive"] = True
                with patch("arch_games.runtime.launcher.system_profile", return_value={}):
                    launcher.launch(plan)
                self.assertIsNone(calls[0]["stdin"])
                self.assertIsNone(calls[0]["stdout"])
                self.assertFalse(calls[0]["start_new_session"])
            finally:
                db.close()

    def test_malformed_size(self):
        for value in [True, 3.5, "NaN", "-1"]:
            with self.subTest(value=value), self.assertRaises(AppError):
                positive_size(value)

    def test_bulk_catalog_state_queries_are_bounded(self):
        from arch_games.catalog.library import Library
        from arch_games.gamevault.client import Client

        with tempfile.TemporaryDirectory() as temp:
            config = Config(temp)
            config.values.update(server_url="https://fixture.invalid", account="fixture")
            db = Database(config.data_dir / "state.db")
            try:
                client = Client(config, db)
                db.put(
                    "library",
                    client.cache_key,
                    {
                        "games": [{"id": n + 1, "title": "Fixture"} for n in range(2000)],
                        "synced_at": "fixture-time",
                    },
                )
                statements = []
                db.connection.set_trace_callback(statements.append)
                library = Library(config, db, {}).load("gamevault", offline=True)
                self.assertEqual(len(library.games), 2000)
                self.assertLessEqual(len(statements), 12)  # constant, independent of the 2000 games
            finally:
                db.close()
