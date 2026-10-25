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
        job = manager.add(self.game)
        manager.run(
            lambda current: manager.cancel(job["id"], cleanup=True) if current["state"] == "running" else None
        )
        self.assertEqual(manager.get(job["id"])["state"], "cancelled")
        self.assertFalse(manager.path(job, True).exists())
