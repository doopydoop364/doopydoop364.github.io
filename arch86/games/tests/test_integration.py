"""Full local workflow over real loopback HTTP; no private services or games."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import base64
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile

from arch_games.catalog.library import Library
from arch_games.catalog.pacman import visible
from arch_games.cli import main
from arch_games.compatibility.archives import inspect_archive
from arch_games.compatibility.feedback import save_feedback
from arch_games.config import Config
from arch_games.downloads.manager import DownloadManager
from arch_games.gamevault.client import Client
from arch_games.runtime.installer import Installer
from arch_games.runtime.launcher import Launcher
from arch_games.runtime.prefixes import PrefixManager
from arch_games.storage.database import Database
from test_compatibility import profile
from test_detection import pe


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Game/Game.exe", pe())
            archive.writestr("Game/README.txt", "Synthetic integration fixture; not a real game.")
        self.payload = stream.getvalue()
        self.requests = []
        payload, requests = self.payload, self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, value, kind="application/json", status=200, **headers):
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                for key, val in headers.items():
                    self.send_header(key.replace("_", "-"), val)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                requests.append((self.command, self.path, self.headers.get("Range")))
                path = urlsplit(self.path).path
                if path == "/api/auth/basic/login":
                    expected = "Basic " + base64.b64encode(b"fixture:fixture-password").decode()
                    if self.headers.get("Authorization") != expected:
                        return self.respond({}, status=401)
                    return self.respond(
                        {"access_token": "fixture-access", "refresh_token": "fixture-refresh"}
                    )
                if self.headers.get("Authorization") != "Bearer fixture-access":
                    return self.respond({}, status=401)
                game = {
                    "id": 1,
                    "title": "Synthetic Game",
                    "type": "WINDOWS_PORTABLE",
                    "metadata": {"title": "Synthetic Game", "description": "Fixture", "cover": {"id": 7}},
                    "versions": [
                        {
                            "id": 11,
                            "type": "WINDOWS_PORTABLE",
                            "size": str(len(payload)),
                            "file_path": "Game.zip",
                            "version": "fixture-1",
                        }
                    ],
                }
                if path == "/api/users/me":
                    return self.respond({"id": 1, "username": "fixture"})
                if path == "/api/games":
                    return self.respond({"data": [game], "meta": {"currentPage": 1, "totalPages": 1}})
                if path == "/api/games/1":
                    return self.respond(game)
                if path == "/api/game/1/versions/11":
                    offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
                    headers = {"ETag": '"fixture-v1"'}
                    if offset:
                        headers["Content_Range"] = "bytes {}-{}/{}".format(
                            offset, len(payload) - 1, len(payload)
                        )
                    return self.respond(
                        payload[offset:], "application/zip", 206 if offset else 200, **headers
                    )
                return self.respond({}, status=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_full_workflow_both_architectures(self):
        display = patch.dict("os.environ", {"DISPLAY": ":99"})  # 3.0 requires a graphical session to launch Windows programs
        display.start()
        self.addCleanup(display.stop)
        for arch in ["i686", "x86_64"]:
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as temp:
                config = Config(temp)
                config.values.update(
                    server_url="http://127.0.0.1:" + str(self.server.server_port),
                    allow_http=True,
                    account="fixture",
                )
                config.save()
                db = Database(config.data_dir / "state.db")
                try:
                    client = Client(config, db)
                    client.login("fixture", "fixture-password")
                    self.assertTrue(client.status()["connected"])
                    self.assertFalse(client.details("1")[1])
                    measured = profile(arch)

                    def packages(args, **kwargs):
                        return SimpleNamespace(
                            returncode=0, stdout="nethack\nopenttd" if args[-1] == "-Slq" else "nethack"
                        )

                    with patch(
                        "arch_games.catalog.library.visible",
                        side_effect=lambda games=None: visible(games, run=packages),
                    ):
                        library = Library(config, db, measured).load()
                        game = library.find("gamevault:1@11")
                        self.assertEqual(len(library.filtered()[0]), 2)
                        manager = DownloadManager(config, db, client)
                        job = manager.add(game)
                        # Resume an interrupted transfer using actual HTTP range handling.
                        manager.path(job, True).write_bytes(self.payload[:100])
                        job["validator"] = '"fixture-v1"'
                        db.put("downloads", job["id"], job)
                        manager.run()
                        done = manager.get(job["id"])
                        self.assertEqual(done["state"], "completed")
                        archive = manager.path(done)
                        self.assertEqual(archive.read_bytes(), self.payload)
                        analyzed = inspect_archive(archive)["entries"][0]["executable"]
                        db.put(
                            "analysis", game.id, {"executable": analyzed, "requirements": {"graphics": "2d"}}
                        )
                        self.assertEqual(
                            Library(config, db, measured).load().reports[game.id]["status"], "Promising"
                        )
                        manifest = Installer(config, db).install(game, archive)
                        self.assertEqual(manifest["game_id"], game.id)

                        def wine(args, **kwargs):
                            if "--version" in args:
                                return SimpleNamespace(returncode=0, stdout="wine-fixture")
                            if args[0].endswith("wineserver"):
                                return SimpleNamespace(returncode=0)
                            root = Path(kwargs["env"]["WINEPREFIX"])
                            (root / "system.reg").write_text(
                                "WINE REGISTRY Version 2\n#arch=" + kwargs["env"]["WINEARCH"] + "\n"
                            )
                            (root / "user.reg").write_text(
                                "WINE REGISTRY Version 2\n#arch=" + kwargs["env"]["WINEARCH"] + "\n"
                            )
                            (root / "drive_c").mkdir()
                            return SimpleNamespace(returncode=0)

                        PrefixManager(config, db, wine, lambda name: "/usr/bin/" + name).create(
                            game.id, "win32" if arch == "i686" else "win64"
                        )
                        launcher = Launcher(
                            config,
                            db,
                            which=lambda name: "/usr/bin/" + name,
                            popen=lambda *a, **k: SimpleNamespace(wait=lambda: 0),
                        )
                        plan = launcher.plan(game.id, "Game/Game.exe")
                        with patch("arch_games.runtime.launcher.system_profile", return_value=measured):
                            record = launcher.launch(plan)
                        self.assertEqual(record["exit_code"], 0)
                        # A zero exit code is not verification; user feedback is a separate action.
                        self.assertNotEqual(
                            Library(config, db, measured).load().reports[game.id]["status"],
                            "Verified Working",
                        )
                        save_feedback(
                            db,
                            game.id,
                            "works",
                            measured,
                            notes="Simulated user attestation for workflow test",
                        )
                        self.assertEqual(
                            Library(config, db, measured).load().reports[game.id]["status"],
                            "Verified Working",
                        )
                        with (
                            patch("arch_games.catalog.library.system_profile", return_value=measured),
                            redirect_stdout(io.StringIO()) as output,
                            redirect_stderr(io.StringIO()),
                        ):
                            self.assertEqual(
                                main(["--state-dir", temp, "list", "--all", "--offline", "--json"]), 0
                            )
                            self.assertIn("Verified Working", output.getvalue())
                        Installer(config, db).uninstall(game.id)
                        self.assertTrue(archive.is_file())
                    self.assertTrue(any(request[2] == "bytes=100-" for request in self.requests))
                    self.assertTrue(all(method == "GET" for method, _, _ in self.requests))
                finally:
                    db.close()
