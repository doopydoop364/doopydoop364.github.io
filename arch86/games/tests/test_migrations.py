import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from arch_games.config import AppError
from arch_games.storage.database import Database
from arch_games.storage.migrations import CURRENT_VERSION, schema_version


def make_v2_0_database(path):
    """Representative Arch Games 2.0 layout: bare records table, no schema marker."""
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE records (namespace TEXT, key TEXT, value TEXT, updated TEXT, PRIMARY KEY(namespace,key))"
    )
    rows = [
        ("prefixes", "gamevault:32", {"directory": "undertale--abc", "state": "ready", "requested_architecture": "win64"}),
        ("installs", "local:x", {"directory": "x--1", "archive_sha256": "ab"}),
        ("launches", "l1", {"game_id": "gamevault:32", "state": "exited", "exit_code": 0}),
    ]
    for ns, key, value in rows:
        c.execute("INSERT INTO records VALUES (?,?,?,?)", (ns, key, json.dumps(value), "2026-01-01T00:00:00+00:00"))
    c.commit()
    c.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.db"

    def test_upgrade_preserves_records_and_backs_up(self):
        make_v2_0_database(self.path)
        db = Database(self.path)
        self.addCleanup(db.close)
        self.assertEqual(schema_version(db.connection), CURRENT_VERSION)
        self.assertEqual(db.get("prefixes", "gamevault:32")["state"], "ready")
        self.assertEqual(len(db.all("launches")), 1)
        self.assertIsNotNone(db.backup_path)
        backup = sqlite3.connect(db.backup_path)
        self.assertEqual(backup.execute("SELECT COUNT(*) FROM records").fetchone()[0], 3)
        self.assertEqual(oct(db.backup_path.stat().st_mode & 0o777), "0o600")
        backup.close()

    def test_idempotent_second_open_makes_no_backup(self):
        make_v2_0_database(self.path)
        Database(self.path).close()
        db = Database(self.path)
        self.addCleanup(db.close)
        self.assertIsNone(db.backup_path)
        self.assertEqual(len(db.all("prefixes")), 1)

    def test_fresh_database_has_no_backup(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        self.assertIsNone(db.backup_path)
        self.assertEqual(schema_version(db.connection), CURRENT_VERSION)

    def test_newer_schema_refused_and_untouched(self):
        Database(self.path).close()
        c = sqlite3.connect(self.path)
        c.execute("UPDATE schema_meta SET value='99' WHERE key='version'")
        c.commit()
        c.close()
        with self.assertRaises(AppError):
            Database(self.path)

    def test_failed_migration_rolls_back(self):
        make_v2_0_database(self.path)
        from arch_games.storage import migrations

        def boom(connection):
            connection.execute("CREATE TABLE half_done (x)")
            raise RuntimeError("fixture failure")

        original = migrations.MIGRATIONS[2]
        migrations.MIGRATIONS[2] = boom
        self.addCleanup(lambda: migrations.MIGRATIONS.__setitem__(2, original))
        with self.assertRaises(RuntimeError):
            Database(self.path)
        c = sqlite3.connect(self.path)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("half_done", tables)
        self.assertNotIn("schema_meta", tables)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM records").fetchone()[0], 3)
        c.close()
