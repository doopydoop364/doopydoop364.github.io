"""End-to-end acceptance workflow (tests A, B, C, H, J-style) with a loopback GameVault, a real download and extraction,
real prefix management through fake wine/wineboot/wineserver scripts, discovery, registration, launch by name and compatibility.

Nothing here runs real Wine or a real game: the fake `wine` only records its invocations and creates the GOG file layout."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import base64
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile

from arch_games.cli import main
from arch_games.config import Config
from arch_games.gamevault.client import Client
from arch_games.storage.database import Database
from test_detection import pe
from test_prefix_recovery import fake_tools

WINE = """#!{python}
import json, os, sys
from pathlib import Path
log = Path({log!r})
args = sys.argv[1:]
if args[:1] == ['--version']:
    print('wine-11.20 e2e-fixture'); sys.exit(0)
with log.open('a') as stream:
    stream.write(json.dumps({{'argv': args, 'prefix': os.environ.get('WINEPREFIX'), 'cwd': os.getcwd()}}) + '\\n')
exe = Path(args[0]) if args else None
if exe and exe.name.lower().startswith('setup_'):
    game = Path(os.environ['WINEPREFIX']) / 'drive_c' / 'GOG Games' / 'Undertale'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'UNDERTALE.exe').write_bytes({pe_game!r})
    (game / 'unins000.exe').write_bytes({pe_uninst!r})
    (game / 'data.win').write_bytes(b'x' * 100)
    (game / 'goggame-1207659032.info').write_text(json.dumps({{'name': 'Undertale', 'playTasks': [{{'isPrimary': True, 'category': 'game', 'path': 'UNDERTALE.exe'}}]}}))
"""


class E2E(unittest.TestCase):
    def setUp(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("Undertale GOG 1.08/setup_undertale_1.08_(18328).exe", pe(32))
            z.writestr("Undertale GOG 1.08/setup_undertale_1.08_(18328)-1.bin", b"payload" * 1000)
        self.payload = stream.getvalue()
        self.requests = []
        payload, requests = self.payload, self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def respond(self, value, kind="application/json", status=200, **headers):
                data = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers.items():
                    self.send_header(k.replace("_", "-"), v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                requests.append(self.path)
                path = urlsplit(self.path).path
                if path == "/api/auth/basic/login":
                    ok = "Basic " + base64.b64encode(b"u:p").decode()
                    return self.respond({"access_token": "a", "refresh_token": "r"}) if self.headers.get("Authorization") == ok else self.respond({}, status=401)
                if self.headers.get("Authorization") != "Bearer a":
                    return self.respond({}, status=401)
                game = {"id": 32, "title": "Undertale", "type": "WINDOWS_SETUP", "metadata": {"title": "Undertale", "genres": [{"name": "RPG"}]},
                        "versions": [{"id": 34, "type": "WINDOWS_SETUP", "size": str(len(payload)), "file_path": "Undertale.zip", "version": "GOG 1.08"}]}
                if path == "/api/users/me":
                    return self.respond({"id": 1})
                if path == "/api/games":
                    return self.respond({"data": [game], "meta": {"currentPage": 1, "totalPages": 1}})
                if path == "/api/game/32/versions/34":
                    return self.respond(payload, "application/zip", ETag='"v1"')
                return self.respond({}, status=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = root / "state"
        self.tools = fake_tools(root / "tools")
        self.wine_log = root / "wine.log"
        wine = root / "tools" / "wine"
        wine.write_text(WINE.format(python=sys.executable, log=str(self.wine_log), pe_game=pe(32) + bytes(2_000_000), pe_uninst=pe(32) + b"Inno Setup Setup Data" + bytes(300_000)))
        wine.chmod(0o700)
        config = Config(self.state)
        config.values.update(server_url="http://127.0.0.1:%d" % self.server.server_port, allow_http=True, account="u")
        config.save()
        db = Database(config.data_dir / "state.db")
        Client(config, db).login("u", "p")
        db.close()
        self.config = config

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        env = {"PATH": str(Path(self.temp.name) / "tools") + os.pathsep + os.environ["PATH"], "DISPLAY": ":99"}
        with patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", str(self.state), "--non-interactive", *argv])
        return code, out.getvalue(), err.getvalue()

    def wine_runs(self):
        return [json.loads(line) for line in self.wine_log.read_text().splitlines()] if self.wine_log.exists() else []

    def downloads(self):
        return [p for p in self.requests if p.startswith("/api/game/32/versions/34")]

    def test_name_to_launch_workflow(self):
        # GameVault sync, friendly name
        self.assertEqual(self.cli("gamevault", "list")[0], 0)
        self.assertEqual(self.cli("alias", "add", "ut", "gamevault:32@34")[0], 0)
        code, out, err = self.cli("info", "undertale")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["id"], "gamevault:32@34")

        # B: install resolves the name, downloads once, verifies, plans, and stops before running the installer
        code, out, err = self.cli("install", "undertale", "--yes")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report["status"], "awaiting_approval", report)
        self.assertEqual(report["plan"]["method"], "wine-installer")
        self.assertEqual(report["plan"]["installer_architecture"], "i686")
        self.assertEqual(len(self.downloads()), 1)
        self.assertEqual([r for r in self.wine_runs() if r["argv"] and "setup_" in r["argv"][0]], [])  # installer not run yet
        self.assertIn("--approve-installer", " ".join(report["next"]))
        prefix_ok = json.loads(self.cli("prefix", "check", "undertale")[1])  # C: prefix managed through the real PrefixManager
        self.assertTrue(prefix_ok["healthy"])
        self.assertEqual(prefix_ok["architecture"], "win64")

        # explicit approval runs the installer, discovery finds the game executable, uninstaller is not offered
        code, out, err = self.cli("install", "undertale", "--yes", "--approve-installer")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report["status"], "done", report)
        self.assertTrue(report["launcher"].endswith("UNDERTALE.exe"))
        self.assertEqual(len(self.downloads()), 1)  # no second download
        installer_runs = [r for r in self.wine_runs() if "setup_" in r["argv"][0]]
        self.assertEqual(len(installer_runs), 1)
        self.assertEqual(Path(installer_runs[0]["prefix"]).name, prefix_ok["path"].rsplit("/", 1)[1])

        # A: launch by name, no WINEPREFIX set by the user
        code, out, err = self.cli("launch", "undertale")
        self.assertEqual(code, 0, err)
        game_runs = [r for r in self.wine_runs() if r["argv"][0].endswith("UNDERTALE.exe")]
        self.assertEqual(len(game_runs), 1)
        self.assertEqual(game_runs[0]["prefix"], prefix_ok["path"])
        self.assertTrue(game_runs[0]["cwd"].endswith("Undertale"))
        code, out, _ = self.cli("launch", "Undertale", "--dry-run")
        self.assertEqual(json.loads(out)["prefix"], "gamevault:32@34")

        # B again: already installed, nothing re-downloaded or overwritten
        code, out, _ = self.cli("install", "ut", "--yes", "--approve-installer")
        self.assertEqual(json.loads(out)["status"], "already_installed")
        self.assertEqual(len(self.downloads()), 1)
        self.assertEqual(len([r for r in self.wine_runs() if "setup_" in r["argv"][0]]), 1)

        # compatibility: started != verified; only the user's confirmation on this machine verifies
        code, out, _ = self.cli("compat", "show", "undertale")
        data = json.loads(out)
        self.assertEqual(data["classification"], "Likely compatible", data)
        self.assertEqual(data["facts"]["game_executable"]["architecture"], "i686")
        self.assertEqual(data["facts"]["installer"]["architecture"], "i686")
        self.assertEqual(data["record"]["launch_status"], "exited_ok")
        self.assertEqual(data["record"]["gameplay_status"], "unverified")
        self.cli("compat", "set", "undertale", "--gameplay", "works", "--audio", "works")
        self.assertEqual(json.loads(self.cli("compat", "show", "undertale")[1])["classification"], "Verified working")

        # saves and history use the same identity
        self.assertEqual(self.cli("playtime", "undertale")[0], 0)
        self.assertEqual(self.cli("wine", "status", "undertale")[0], 0)
        code, out, _ = self.cli("repair", "undertale", "--dry-run")
        self.assertEqual(code, 0)

        # H/J: the database is a valid migrated 3.0 database with every 2.0 namespace intact
        db = Database(self.config.data_dir / "state.db")
        try:
            self.assertEqual(db.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            for ns in ("prefixes", "installs", "downloads", "launches", "launchers", "install_jobs", "compat"):
                self.assertTrue(db.all(ns), ns)
        finally:
            db.close()

    def test_ambiguity_and_unknown_names_are_errors_not_guesses(self):
        self.cli("gamevault", "list")
        code, _, err = self.cli("launch", "completely unknown game")
        self.assertEqual(code, 1)
        self.assertIn("No game matches", err)
