"""Acceptance test J: interruptions and failures leave actionable, recoverable states and an intact database."""

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest
import zipfile

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.installation.workflow import Options, Workflow
from arch_games.runtime.launcher import Launcher
from arch_games.storage.database import Database
from test_detection import pe
from test_install_workflow import Base as InstallBase, FakeProcess, GAME, StubPrefixes
from test_downloads import Client, Response


class CorruptDatabaseTests(unittest.TestCase):
    def test_damaged_database_is_reported_not_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "s"
            config = Config(state)
            db_path = config.data_dir / "state.db"
            junk = b"this is not a sqlite database" * 100
            db_path.write_bytes(junk)
            db_path.chmod(0o600)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["--state-dir", str(state), "--non-interactive", "history"])
            self.assertEqual(code, 1)
            self.assertIn("damaged", err.getvalue())
            self.assertIn("backup", err.getvalue())
            self.assertEqual(db_path.read_bytes(), junk)  # nothing was wiped to "fix" it


class InterruptedInstallTests(InstallBase):
    def zip_installer(self):
        return self.gog_zip()

    def test_ctrl_c_during_installer_marks_job_interrupted_and_resumes(self):
        archive = self.zip_installer()
        calls = {"n": 0}
        original = self.popen

        def interrupted(argv, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt()
            return original(argv, **kw)

        self.launcher.popen = interrupted
        options = Options(archive=archive, yes=True, approve_installer=True)
        with self.assertRaises(KeyboardInterrupt):
            self.flow().run(GAME, "Undertale", options)
        job = self.db.get("install_jobs", GAME)
        self.assertEqual(job["status"], "interrupted")
        self.assertIn("resume", job["error"])
        self.assertTrue(self.db.get("installs", GAME))  # extraction survived
        self.assertEqual(self.db.all("install_transactions"), [])  # no half-done file operation left behind
        report = self.flow().run(GAME, "Undertale", options)
        self.assertEqual(report["status"], "done", report)

    def test_missing_wine_during_prefix_creation_is_resumable_and_data_safe(self):
        archive = self.zip("p.zip", {"Game/Celeste.exe": pe(64) + bytes(2_000_000), "Game/data.pak": b"x"})

        class NoWinePrefixes(StubPrefixes):
            def create(self, ident, arch, **kw):
                raise AppError("Wine/wineboot/wineserver is missing; install an appropriate runtime explicitly")

        flow = Workflow(self.config, self.db, say=lambda m: None, launcher=self.launcher, prefixes=NoWinePrefixes(self.config, self.db))
        with self.assertRaises(AppError):
            flow.run("local:celeste", "Celeste", Options(archive=archive, yes=True))
        job = self.db.get("install_jobs", "local:celeste")
        self.assertEqual(job["status"], "failed")
        self.assertIn("Wine", job["error"])
        self.assertTrue(self.db.get("installs", "local:celeste"))  # files kept for the retry
        self.assertEqual(self.db.get("compat", [r["key"] for r in self.db.all("compat") if "celeste" in r["key"]][0])["installation_status"], "failed")
        report = self.flow().run("local:celeste", "Celeste", Options(archive=archive, yes=True))  # wine "installed": retry succeeds
        self.assertEqual(report["status"], "done", report)

    def test_corrupt_archive_fails_cleanly(self):
        bad = Path(self.temp.name) / "bad.zip"
        bad.write_bytes(b"PK\x03\x04 truncated")
        with self.assertRaises(Exception):
            self.flow().run("local:bad", "Bad", Options(archive=str(bad), yes=True))
        job = self.db.get("install_jobs", "local:bad")
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(self.db.get("installs", "local:bad"))
        self.assertEqual(self.db.all("install_transactions"), [])
        self.assertEqual([p.name for p in self.config.directory("installed").iterdir() if p.name.startswith(".staging")], [])


class InterruptedDownloadTests(unittest.TestCase):
    def test_killed_download_is_recovered_as_resumable_queue_item(self):
        from arch_games.downloads.manager import DownloadManager
        from arch_games.models import Game

        with tempfile.TemporaryDirectory() as d:
            config = Config(d)
            config.values.update(server_url="https://fixture.invalid", account="fixture")
            db = Database(config.data_dir / "state.db")
            try:
                game = Game("gamevault:1", "G", "gamevault", metadata={"remote": {"id": 1, "size": 10, "file_path": "g.zip"}})
                manager = DownloadManager(config, db, Client([]))
                job = manager.add(game)
                part = manager.path(job, True)
                part.write_bytes(b"01234")  # 5 bytes were written before the process died
                job.update(state="running", received=5, validator='"v"')  # ... and the state still says running
                db.put("downloads", job["id"], job)
                manager.client.responses = [Response(b"56789", status=206, Content_Length=5, ETag='"v"', Content_Range="bytes 5-9/10")]
                manager.run()
                done = manager.get(job["id"])
                self.assertEqual(done["state"], "completed")
                self.assertEqual(manager.path(done).read_bytes(), b"0123456789")
                self.assertEqual(manager.client.headers[-1]["Range"], "bytes=5-")
            finally:
                db.close()


class FailedLaunchTests(InstallBase):
    def test_failed_launch_is_recorded_with_actionable_status(self):
        from arch_games.runtime.launchers import LauncherStore

        game = self.prefix / "drive_c" / "Games" / "G"
        game.mkdir(parents=True)
        (game / "Game.exe").write_bytes(pe(32) + bytes(2_000_000))
        LauncherStore(self.config, self.db).register(GAME, "G", str(game / "Game.exe"))
        self.exit_code = 53
        plan = self.launcher.plan_registered(GAME)
        record = self.launcher.launch(plan)
        self.assertEqual(record["exit_code"], 53)
        from arch_games.runtime import winetools

        status = winetools.status(self.config, self.db, GAME, which=lambda n: "/usr/bin/" + n)
        self.assertTrue(any("exited with status 53" in p for p in status["problems"]))
        compat = [r["value"] for r in self.db.all("compat")][0]
        self.assertEqual(compat["launch_status"], "exited_error")
        self.assertNotEqual(compat["gameplay_status"], "works")
