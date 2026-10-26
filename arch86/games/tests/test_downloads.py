import no_probe  # noqa: F401  (must come first: disables real system probes)
from email.message import Message
import io
import tempfile
from types import SimpleNamespace
import unittest
from arch_games.config import AppError, Config
from arch_games.downloads.manager import DownloadManager
from arch_games.models import Game
from arch_games.storage.database import Database


class Response(io.BytesIO):
    def __init__(self, data, status=200, **headers):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for k, v in headers.items():
            self.headers[k.replace("_", "-")] = str(v)


class Client:
    def __init__(self, responses):
        self.responses, self.headers = list(responses), []

    def download_path(self, *args):
        return "/api/games/1/download"

    def open(self, path, headers):
        self.headers.append(headers)
        return self.responses.pop(0)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.config.values.update(server_url="https://example.invalid", account="fixture")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.game = Game(
            "gamevault:1",
            "Game",
            "gamevault",
            metadata={"remote": {"id": 1, "size": "6", "file_path": "../Game.zip"}},
        )

    def manager(self, responses):
        return DownloadManager(self.config, self.db, Client(responses))

    def test_complete_and_no_false_checksum_claim(self):
        manager = self.manager([Response(b"abcdef", Content_Length=6, ETag='"one"')])
        job = manager.add(self.game)
        manager.run()
        done = manager.get(job["id"])
        self.assertEqual(done["state"], "completed")
        self.assertEqual(done["verification"], "size-only")
        self.assertEqual(manager.path(done).read_bytes(), b"abcdef")
        self.assertNotIn("/", done["filename"])

    def test_resume(self):
        manager = self.manager(
            [Response(b"def", 206, Content_Length=3, Content_Range="bytes 3-5/6", ETag='"one"')]
        )
        job = manager.add(self.game)
        manager.path(job, True).write_bytes(b"abc")
        job["validator"] = '"one"'
        self.db.put("downloads", job["id"], job)
        manager.run()
        self.assertEqual(manager.client.headers[0]["Range"], "bytes=3-")
        self.assertEqual(manager.path(job).read_bytes(), b"abcdef")

    def test_ignored_range_and_unvalidated_partial(self):
        for validator in ['"one"', None]:
            manager = self.manager([Response(b"abcdef", Content_Length=6)])
            job = manager.add(self.game)
            manager.path(job, True).write_bytes(b"abc")
            job["validator"] = validator
            self.db.put("downloads", job["id"], job)
            manager.run()
            self.assertEqual(manager.path(job).read_bytes(), b"abcdef")

    def test_failures_and_cancel(self):
        manager = self.manager([Response(b"def", 206, Content_Length=3, Content_Range="bytes 2-4/6")])
        job = manager.add(self.game)
        manager.path(job, True).write_bytes(b"abc")
        job["validator"] = '"one"'
        self.db.put("downloads", job["id"], job)
        manager.run()
        self.assertEqual(manager.get(job["id"])["state"], "failed")
        manager.cancel(job["id"], True)
        self.assertFalse(manager.path(job, True).exists())
        manager = self.manager([])
        job = manager.add(self.game)
        manager.disk_usage = lambda p: SimpleNamespace(free=1)
        manager.run()
        self.assertIn("disk space", manager.get(job["id"])["error"])

    def test_checksum_mismatch_and_symlink(self):
        manager = self.manager([Response(b"abcdef", Content_Length=6)])
        job = manager.add(self.game, checksum="0" * 64)
        manager.run()
        self.assertIn("checksum mismatch", manager.get(job["id"])["error"])
        self.assertFalse(manager.path(job).exists())
        job = manager.add(self.game)
        (manager.root / (job["filename"] + ".part")).symlink_to("/tmp/never-touch")
        with self.assertRaises(AppError):
            manager.path(job, True)

    def test_zero_length_and_active_cancel(self):
        manager = self.manager([Response(b"", Content_Length=0)])
        self.game.metadata["remote"]["size"] = "0"
        job = manager.add(self.game)
        manager.run()
        self.assertEqual(manager.get(job["id"])["state"], "completed")
        self.game.metadata["remote"]["size"] = "6"
        manager = self.manager([Response(b"abcdef", Content_Length=6, ETag='"one"')])
        job = manager.add(self.game, force=True)  # 3.0 reuses a completed identical archive unless forced
        manager.run(
            lambda current: manager.cancel(job["id"], cleanup=True) if current["state"] == "running" else None
        )
        self.assertEqual(manager.get(job["id"])["state"], "cancelled")
        self.assertFalse(manager.path(job, True).exists())

    def test_queue_runs_in_creation_order(self):
        from unittest.mock import patch

        manager = self.manager([Response(b"abcdef", Content_Length=6), Response(b"fedcba", Content_Length=6)])
        identifiers = iter([SimpleNamespace(hex=value) for value in ["f" * 32, "e" * 32, "0" * 32, "1" * 32]])
        with patch("arch_games.downloads.manager.uuid.uuid4", side_effect=lambda: next(identifiers)):
            first = manager.add(self.game)
            second = manager.add(self.game)
        first["created_at"], second["created_at"] = "2026-01-01", "2026-01-02"
        self.db.put("downloads", first["id"], first)
        self.db.put("downloads", second["id"], second)
        completed = []
        manager.run(lambda job: completed.append(job["id"]) if job["state"] == "completed" else None)
        self.assertEqual(completed, [first["id"], second["id"]])


class QueueManagementTests(unittest.TestCase):
    """Priorities, pause/resume, duplicate-archive handling."""

    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.config.values.update(server_url="https://fixture.invalid", account="fixture")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)

    def game(self, n):
        from arch_games.models import Game

        return Game("gamevault:%d" % n, "Game %d" % n, "gamevault", metadata={"remote": {"id": n, "size": 6, "file_path": "g%d.zip" % n}})

    def manager(self, responses):
        return DownloadManager(self.config, self.db, Client(responses))

    def test_priority_orders_the_queue_and_is_validated(self):
        m = self.manager([])
        slow = m.add(self.game(1), priority=9)
        fast = m.add(self.game(2), priority=0)
        mid = m.add(self.game(3))
        order = [r["value"]["id"] for r in sorted(self.db.all("downloads"), key=lambda r: (r["value"]["priority"], r["value"]["created_at"]))]
        self.assertEqual(order, [fast["id"], mid["id"], slow["id"]])
        m.set_priority(slow["id"], 1)
        self.assertEqual(m.get(slow["id"])["priority"], 1)
        for bad in (-1, 10, "x", None):
            with self.assertRaises(AppError):
                m.set_priority(fast["id"], bad)
            with self.assertRaises(AppError):
                m.add(self.game(4), priority=bad)

    def test_pause_queued_and_resume(self):
        m = self.manager([])
        job = m.add(self.game(1))
        self.assertEqual(m.pause(job["id"])["state"], "paused")
        m.run()  # paused jobs are skipped
        self.assertEqual(m.get(job["id"])["state"], "paused")
        self.assertEqual(m.retry(job["id"])["state"], "queued")
        with self.assertRaises(AppError):
            m.pause("nope")

    def test_pause_during_transfer_keeps_partial_and_resume_continues(self):
        game = self.game(1)
        game.metadata["remote"]["size"] = 600000
        body = b"x" * 600000
        m = self.manager([Response(body, Content_Length=600000, ETag='"v1"')])
        job = m.add(game)
        m.run(lambda current: m.pause(job["id"]) if current["state"] == "running" else None)
        paused = m.get(job["id"])
        self.assertEqual(paused["state"], "paused")
        part = m.path(paused, True)
        self.assertTrue(part.exists())
        have = part.stat().st_size
        self.assertTrue(0 < have < 600000)
        self.assertIsNone(self.db.get("download_cancel", job["id"]))  # flag consumed, retry is clean
        m.retry(job["id"])
        m.client.responses = [Response(body[have:], status=206, Content_Length=600000 - have, ETag='"v1"', Content_Range="bytes {}-599999/600000".format(have))]
        m.run()
        done = m.get(job["id"])
        self.assertEqual(done["state"], "completed")
        self.assertEqual(m.path(done).read_bytes(), body)
        self.assertEqual(m.client.headers[-1]["Range"], "bytes={}-".format(have))

    def test_completed_archive_is_reused_not_redownloaded(self):
        m = self.manager([])
        job = m.add(self.game(1))
        (self.config.directory("downloads") / job["filename"]).write_bytes(b"abcdef")
        job.update(state="completed", sha256="x")
        self.db.put("downloads", job["id"], job)
        again = m.add(self.game(1))
        self.assertTrue(again["reused"])
        self.assertEqual(again["id"], job["id"])
        self.assertEqual(len(self.db.all("downloads")), 1)
        forced = m.add(self.game(1), force=True)
        self.assertNotEqual(forced["id"], job["id"])
        (self.config.directory("downloads") / job["filename"]).unlink()  # file gone -> a new download is legitimate
        self.assertNotIn("reused", m.add(self.game(1)))

    def test_dedupe_hardlinks_identical_archives_and_deletes_nothing(self):
        import hashlib

        m = self.manager([])
        digest = hashlib.sha256(b"same-bytes").hexdigest()
        for n, stamp in ((1, "2026-01-01"), (2, "2026-01-02")):
            job = m.add(self.game(n), force=True)
            (self.config.directory("downloads") / job["filename"]).write_bytes(b"same-bytes")
            job.update(state="completed", sha256=digest, created_at=stamp)
            self.db.put("downloads", job["id"], job)
        other = m.add(self.game(3))
        (self.config.directory("downloads") / other["filename"]).write_bytes(b"different")
        other.update(state="completed", sha256=hashlib.sha256(b"different").hexdigest())
        self.db.put("downloads", other["id"], other)
        preview = m.dedupe()
        self.assertEqual(len(preview), 1)
        self.assertFalse(preview[0]["applied"])
        self.assertFalse(os.path.samefile(preview[0]["kept"], preview[0]["duplicate"]))
        result = m.dedupe(apply=True)
        self.assertTrue(result[0]["applied"])
        self.assertTrue(os.path.samefile(result[0]["kept"], result[0]["duplicate"]))
        self.assertEqual(Path(result[0]["duplicate"]).read_bytes(), b"same-bytes")  # both entries still readable
        self.assertEqual(m.dedupe(), [])  # nothing left to do


import os  # noqa: E402
from pathlib import Path  # noqa: E402
