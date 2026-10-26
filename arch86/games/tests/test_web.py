"""Web GUI: real HTTP server on loopback with real sessions; services shared with the CLI."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
from contextlib import redirect_stdout
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.storage.database import Database
from arch_games.ui.web.server import WebApp, load_token
from test_discovery_launch import GAME, build_undertale


class WebCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.config = Config(self.state)
        db = Database(self.config.data_dir / "state.db")
        self.prefix, self.game_dir = build_undertale(self.config, db)
        from arch_games.runtime.launchers import LauncherStore

        LauncherStore(self.config, db).register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"))
        db.put("aliases", "undertale", {"alias": "undertale", "game_id": GAME})
        db.close()
        self.app = WebApp(self.config, port=0)
        self.port = self.app.start()
        threading.Thread(target=self.app.serve_forever, daemon=True).start()
        self.addCleanup(self.app.stop)

    def request(self, method, path, body=None, headers=None, host=None, cookie=None, csrf=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if cookie:
            h["Cookie"] = cookie
        if csrf:
            h["X-CSRF-Token"] = csrf
        data = raw
        if body is not None:
            data = json.dumps(body)
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = payload
        return r.status, parsed, r

    def login(self):
        status, body, r = self.request("POST", "/api/login", {"token": self.app.token})
        assert status == 200, body
        cookie = r.getheader("Set-Cookie").split(";")[0]
        return cookie, body["csrf"]


class AuthTests(WebCase):
    def test_static_pages_public_api_private(self):
        status, body, r = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Arch Games", body)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        for path in ("/api/library", "/api/doctor", "/api/downloads", "/api/game/" + GAME, "/api/history"):
            self.assertEqual(self.request("GET", path)[0], 401, path)

    def test_login_cookie_flags_and_session(self):
        status, _, r = self.request("POST", "/api/login", {"token": self.app.token})
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        status, body, _ = self.request("GET", "/api/session", cookie=cookie.split(";")[0])
        self.assertTrue(body["authenticated"])
        self.assertEqual(self.request("GET", "/api/recent", cookie="ag_session=" + "x" * 30)[0], 401)

    def test_bad_token_and_throttle(self):
        for _ in range(5):
            self.assertEqual(self.request("POST", "/api/login", {"token": "nope"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"token": self.app.token})[0], 429)

    def test_csrf_origin_and_host_checks(self):
        cookie, csrf = self.login()
        body = {"value": True}
        self.assertEqual(self.request("POST", "/api/favorite/" + GAME, body, cookie=cookie)[0], 403)  # no CSRF header
        self.assertEqual(self.request("POST", "/api/favorite/" + GAME, body, cookie=cookie, csrf="wrong")[0], 403)
        self.assertEqual(self.request("POST", "/api/favorite/" + GAME, body, cookie=cookie, csrf=csrf, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/favorite/" + GAME, body, cookie=cookie, csrf=csrf, host="evil.example")[0], 403)  # DNS rebinding
        self.assertEqual(self.request("GET", "/api/recent", cookie=cookie, host="attacker.test:%d" % self.port)[0], 403)
        self.assertEqual(self.request("POST", "/api/login", {"token": self.app.token}, headers={"Origin": "http://evil.example"})[0], 403)
        ok = self.request("POST", "/api/favorite/" + GAME, body, cookie=cookie, csrf=csrf, headers={"Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(ok[0], 200)

    def test_methods_and_body_limits(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request("PUT", "/api/recent", {}, cookie=cookie, csrf=csrf)[0], 405)
        self.assertEqual(self.request("DELETE", "/api/recent", cookie=cookie)[0], 405)
        self.assertEqual(self.request("POST", "/api/notes/" + GAME, raw="x" * 70000, cookie=cookie, csrf=csrf, headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/notes/" + GAME, raw="{bad", cookie=cookie, csrf=csrf, headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("POST", "/api/notes/" + GAME, raw="{}", cookie=cookie, csrf=csrf, headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("POST", "/api/notes/" + GAME, raw="[1]", cookie=cookie, csrf=csrf, headers={"Content-Type": "application/json"})[0], 400)

    def test_no_generic_execution_surface(self):
        cookie, csrf = self.login()
        for path in ("/api/exec", "/api/shell", "/api/run", "/api/launch/anything", "/api/../etc/passwd", "/api/wine/run"):
            status = self.request("POST", path, {"cmd": "id"}, cookie=cookie, csrf=csrf)[0]
            self.assertIn(status, (404, 409), path)
        status, body, _ = self.request("POST", "/api/launch", {"game": "$(id)"}, cookie=cookie, csrf=csrf)
        self.assertEqual(status, 400)
        status, body, _ = self.request("POST", "/api/launch", {"game": GAME, "argv": ["/bin/sh"], "executable": "/bin/sh"}, cookie=cookie, csrf=csrf)
        self.assertNotIn("sh", json.dumps(body).replace("shell", ""))  # extra fields are ignored, never executed


class OperationTests(WebCase):
    def setUp(self):
        super().setUp()
        self.cookie, self.csrf = self.login()

    def call(self, method, path, body=None):
        return self.request(method, path, body, cookie=self.cookie, csrf=self.csrf)

    def test_library_game_and_shared_state_with_cli(self):
        status, body, _ = self.call("GET", "/api/library?source=local&all=1")
        self.assertEqual(status, 200)
        status, body, _ = self.call("GET", "/api/game/" + GAME)
        self.assertEqual(status, 200)
        self.assertEqual(body["launcher"]["exists"], True)
        self.assertEqual(body["aliases"], ["undertale"])
        self.assertEqual(body["prefix"]["state"], "ready")
        self.assertEqual(self.call("POST", "/api/favorite/" + GAME, {"value": True})[0], 200)
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--state-dir", str(self.state), "favorite", "list"])
        self.assertEqual(json.loads(out.getvalue()), [GAME])  # same database, same core as the CLI
        self.assertTrue(self.call("GET", "/api/game/" + GAME)[1]["favorite"])
        self.assertEqual(self.call("POST", "/api/notes/" + GAME, {"text": "great", "tags": ["rpg", "gog"]})[0], 200)
        self.assertEqual(self.call("GET", "/api/game/" + GAME)[1]["tags"], ["gog", "rpg"])
        self.assertEqual(self.call("POST", "/api/notes/" + GAME, {"text": "x", "tags": ["<script>"]})[0], 400)

    def test_input_validation(self):
        for bad in ("../x", "a b", "x" * 200, "nocolon", "UPPER:id!"):
            self.assertEqual(self.call("GET", "/api/game/" + bad.replace(" ", "%20"))[0] in (400, 404), True)
        self.assertEqual(self.call("GET", "/api/library?sort=evil")[0], 400)
        self.assertEqual(self.call("GET", "/api/library?source=evil")[0], 400)
        self.assertEqual(self.call("GET", "/api/cover/abc")[0], 400)
        self.assertEqual(self.call("GET", "/api/job/none")[0], 404)

    def test_wine_settings_validated_and_persisted(self):
        status, body, _ = self.call("POST", "/api/wine/config/" + GAME, {"dll_overrides": {"dxgi": "n,b"}, "debug": "-all"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", "/api/wine/config/" + GAME, {"debug": "x; rm -rf /"})[0], 409)
        self.assertEqual(self.call("POST", "/api/wine/config/" + GAME, {"env": {"LD_PRELOAD": "/x"}})[0], 409)
        self.assertEqual(self.call("GET", "/api/game/" + GAME)[1]["wine_settings"]["dll_overrides"], {"dxgi": "n,b"})
        status, body, _ = self.call("GET", "/api/wine/doctor")
        self.assertEqual(status, 200)
        self.assertIn("checks", body)

    def test_launch_uses_registered_launcher_only(self):
        bindir = Path(self.temp.name) / "bin"
        bindir.mkdir()
        log = Path(self.temp.name) / "wine.log"
        wine = bindir / "wine"
        wine.write_text("#!/bin/sh\nprintf '%s\\n' \"$WINEPREFIX\" \"$@\" > '{}'\n".format(log))
        wine.chmod(0o755)
        env = {"PATH": str(bindir) + os.pathsep + os.environ["PATH"], "DISPLAY": ":99"}
        with patch.dict(os.environ, env):
            status, body, _ = self.call("POST", "/api/launch", {"game": GAME})
            self.assertEqual(status, 200, body)
            for _ in range(50):
                job = self.call("GET", "/api/job/" + body["id"])[1]
                if job["state"] != "running":
                    break
                time.sleep(0.1)
        self.assertEqual(job["state"], "done", job)
        lines = log.read_text().splitlines()
        self.assertEqual(lines[0], str(self.prefix))
        self.assertTrue(lines[1].endswith("UNDERTALE.exe"))

    def test_launch_requires_display_and_launcher(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY", None)
            os.environ.pop("WAYLAND_DISPLAY", None)
            bindir = Path(self.temp.name) / "b2"
            bindir.mkdir()
            (bindir / "wine").write_text("#!/bin/sh\n")
            (bindir / "wine").chmod(0o755)
            with patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
                status, body, _ = self.call("POST", "/api/launch", {"game": GAME})
        self.assertEqual(status, 409)
        self.assertIn("graphical session", body["error"])
        self.assertEqual(self.call("POST", "/api/launch", {"game": "local:nothing"})[0], 409)

    def test_install_approval_requires_prepared_plan_and_matching_hash(self):
        status, body, _ = self.call("POST", "/api/install/approve", {"game": "gamevault:5", "plan_sha256": "a" * 64, "approve_installer": True})
        self.assertEqual(status, 409)
        self.assertIn("Prepare", body["error"])
        db = Database(self.config.data_dir / "state.db")
        db.put("install_jobs", "gamevault:5", {"game_id": "gamevault:5", "status": "plan_only", "source": {"sha256": "b" * 64}, "plan": {"method": "wine-installer"}, "steps": {}})
        db.close()
        status, body, _ = self.call("POST", "/api/install/approve", {"game": "gamevault:5", "plan_sha256": "a" * 64, "approve_installer": True})
        self.assertEqual(status, 409)
        self.assertIn("changed", body["error"])
        self.assertEqual(self.call("GET", "/api/installs")[1][0]["status"], "plan_only")

    def test_downloads_and_audio_and_doctor(self):
        db = Database(self.config.data_dir / "state.db")
        db.put("downloads", "d1", {"id": "d1", "title": "T", "game_id": "gamevault:1", "state": "failed", "received": 5, "expected_size": 10, "error": "net", "filename": "d1-x.zip"})
        db.close()
        rows = self.call("GET", "/api/downloads")[1]
        self.assertEqual(rows[0]["state"], "failed")
        self.assertEqual(self.call("POST", "/api/downloads/cancel", {"id": "d1"})[0], 200)
        status, body, _ = self.call("GET", "/api/audio")
        self.assertEqual(status, 200)
        self.assertFalse(body["remote"]["enabled"])
        status, body, _ = self.call("GET", "/api/doctor")
        self.assertEqual(status, 200)
        self.assertIn("summary", body)
        self.assertEqual(self.call("GET", "/api/storage")[0], 200)


class ConfigTests(unittest.TestCase):
    def test_remote_bind_needs_flag_and_token_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            config = Config(Path(d) / "s")
            with self.assertRaises(AppError):
                WebApp(config, host="0.0.0.0")
            app = WebApp(config, host="0.0.0.0", allow_remote=True, allowed_hosts=["box:8765"])
            self.assertEqual(oct((config.config_dir / "web-token").stat().st_mode & 0o777), "0o600")
            self.assertTrue(app.host_ok("box:8765"))
            self.assertFalse(app.host_ok("evil:8765"))
            (config.config_dir / "web-token").chmod(0o644)
            with self.assertRaises(AppError):
                load_token(config)
            self.assertEqual(load_token.__name__, "load_token")

    def test_static_assets_are_packaged(self):
        from arch_games.ui.web.server import static

        for name in ("index.html", "app.js", "app.css"):
            self.assertGreater(len(static(name)), 100)
        js = static("app.js").decode()
        self.assertNotIn("innerHTML", js)  # titles come from a remote server: only textContent/DOM nodes
