"""Web GUI and native Pacman games: status display, launch, and the safe subset of launch-settings edits."""

import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from arch_games.config import Config
from arch_games.runtime import native
from arch_games.runtime.launchers import LauncherStore
from arch_games.runtime.pacman import PacmanService
from arch_games.storage.database import Database
from arch_games.ui.web.server import WebApp
from native_fixtures import FakeSystem

GID = "pacman:openttd"


class WebNativeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.system = FakeSystem(self.temp.name)
        self.out = Path(self.temp.name) / "ran.txt"
        for ctx in (self.system.active(), patch.dict(os.environ, {"DISPLAY": ":99"}),
                    patch("arch_games.compatibility.graphics.detect_graphics", return_value={"display": True, "accelerated": False, "renderer": "x", "opengl": True, "vulkan": False})):
            ctx.__enter__()
            self.addCleanup(ctx.__exit__, None, None, None)
        self.exe = self.system.openttd(script=True, body='printf "%s\\n" "$SDL_AUDIODRIVER" "$@" > "{}"\n'.format(self.out).encode())
        self.config = Config(Path(self.temp.name) / "state")
        db = Database(self.config.data_dir / "state.db")
        pacman = PacmanService(run=self.system.run)
        candidate = native.discover_native(pacman, "openttd", "OpenTTD")["candidates"][0]
        LauncherStore(self.config, db).register_native(GID, "OpenTTD", "openttd", candidate, pacman)
        db.close()
        self.app = WebApp(self.config, port=0)
        self.port = self.app.start()
        threading.Thread(target=self.app.serve_forever, daemon=True).start()
        self.addCleanup(self.app.stop)
        status, body, r = self.request("POST", "/api/login", {"token": self.app.token})
        self.cookie, self.csrf = r.getheader("Set-Cookie").split(";")[0], body["csrf"]

    def request(self, method, path, body=None, auth=True, csrf=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": "127.0.0.1:%d" % self.port, "Content-Type": "application/json"}
        if auth and getattr(self, "cookie", None):
            headers["Cookie"] = self.cookie
        if auth and csrf and getattr(self, "csrf", None):
            headers["X-CSRF-Token"] = self.csrf
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        return r.status, data, r

    def stored(self):
        db = Database(self.config.data_dir / "state.db")
        try:
            return db.get("launchers", GID)
        finally:
            db.close()

    def wait_job(self, job_id):
        for _ in range(100):
            job = self.request("GET", "/api/job/" + job_id)[1]
            if job["state"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")


class DisplayTests(WebNativeCase):
    def test_game_detail_shows_native_package_status_and_launcher(self):
        status, data, _ = self.request("GET", "/api/game/" + GID)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["native"], {"package": "openttd", "version": "15.3-1", "status": "ready", "label": "Installed and ready to launch", "executable": self.exe})
        self.assertEqual((data["launcher"]["kind"], data["launcher"]["runner"], data["launcher"]["exists"]), ("native-package", "native", True))
        self.assertEqual(data["launcher"]["profiles"], ["default"])

    def test_launcher_state_endpoint_describes_what_is_editable(self):
        status, data, _ = self.request("GET", "/api/launcher/" + GID)
        self.assertEqual((status, data["status"], data["runner"], data["package"]), (200, "ready", "native", "openttd"))
        self.assertEqual(data["editable"], ["arguments", "environment", "profiles", "default_profile"])
        self.assertIn("terminal", data["not_editable_here"])
        self.assertIn("Sensitive", data["environment_policy"])

    def test_library_lists_native_games_with_authoritative_install_state(self):
        rows = self.request("GET", "/api/library?source=pacman&all=1")[1]["games"]
        entry = next(r for r in rows if r["id"] == GID)
        self.assertEqual((entry["title"], entry["installed"]), ("OpenTTD", True))
        self.system.remove_package("openttd")  # removed behind Arch Games' back
        self.app.api._library = None  # (the library cache normally expires after 30 s)
        rows = self.request("GET", "/api/library?source=pacman&all=1")[1]["games"]
        self.assertFalse(next(r for r in rows if r["id"] == GID)["installed"])
        state = self.request("GET", "/api/launcher/" + GID)[1]
        self.assertEqual(state["status"], "package-removed")
        self.assertIn("arch-games install", state["hint"])

    def test_stale_launcher_gets_a_clear_message_and_a_terminal_hint(self):
        Path(self.exe).unlink()
        state = self.request("GET", "/api/launcher/" + GID)[1]
        self.assertEqual(state["status"], "launcher-invalid")
        self.assertIn("arch-games launcher rediscover " + GID, state["hint"])
        status, data, _ = self.request("POST", "/api/launch", {"game": GID})
        self.assertEqual(status, 409)
        self.assertIn("rediscover", data["error"])


class SettingsTests(WebNativeCase):
    def post(self, path, body, **kw):
        return self.request("POST", path, body, **kw)

    def test_save_arguments_and_environment_then_launch_with_them(self):
        status, data, _ = self.post("/api/launcher/" + GID + "/settings", {"arguments": ["-s", "sdl"], "environment": {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["profiles"]["default"], {"arguments": ["-s", "sdl"], "environment": {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}})
        self.assertEqual(self.stored()["arguments"], ["-s", "sdl"])
        status, job, _ = self.post("/api/launch", {"game": GID})
        self.assertEqual(status, 200, job)
        self.assertEqual(self.wait_job(job["id"])["state"], "done")
        self.assertEqual(self.out.read_text().splitlines(), ["alsa", "-s", "sdl"])

    def test_executable_runner_and_working_directory_cannot_be_changed_from_the_browser(self):
        before = self.stored()
        status, data, _ = self.post("/api/launcher/" + GID + "/settings", {"arguments": ["-x"], "environment": {}, "executable": "/bin/sh", "runner": "custom",
                                                                          "working_directory": "/", "command": ["/bin/sh", "-c", "id"], "package": "evil"})
        self.assertEqual(status, 200)
        after = self.stored()
        for key in ("executable", "runner", "working_directory", "package", "resolved_executable"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["arguments"], ["-x"])

    def test_sensitive_reserved_and_malformed_input_is_rejected(self):
        for env in ({"LD_PRELOAD": "/x"}, {"PATH": "/evil"}, {"HOME": "/"}, {"WINEPREFIX": "/x"}, {"bad-name": "x"}, {"A": "x\ny"}, {"A": "x" * 9000}, {"A": 5}):
            status, data, _ = self.post("/api/launcher/" + GID + "/settings", {"arguments": [], "environment": env})
            self.assertEqual(status, 400, env)
        for body in ({"arguments": "not a list", "environment": {}}, {"arguments": [], "environment": ["x"]}, {"arguments": ["a\0b"], "environment": {}}, {"arguments": ["a\nb"], "environment": {}},
                     {"arguments": ["x"] * 100, "environment": {}}, {"arguments": [], "environment": {}, "profile": 5}, {"arguments": [], "environment": {}, "profile": "ghost"}):
            self.assertEqual(self.post("/api/launcher/" + GID + "/settings", body)[0], 400, body)
        self.assertEqual(self.stored()["arguments"], [])
        self.assertEqual(self.stored()["environment"], {})

    def test_arguments_are_not_evaluated(self):
        marker = Path(self.temp.name) / "pwned"
        nasty = ["$(touch {})".format(marker), "; touch {}".format(marker)]
        self.post("/api/launcher/" + GID + "/settings", {"arguments": nasty, "environment": {"X": "`touch {}`".format(marker)}})
        job = self.post("/api/launch", {"game": GID})[1]
        self.assertEqual(self.wait_job(job["id"])["state"], "done")
        self.assertFalse(marker.exists())
        self.assertEqual(self.out.read_text().splitlines()[1:], nasty)

    def test_profiles_create_default_launch_and_delete(self):
        status, data, _ = self.post("/api/launcher/" + GID + "/settings", {"arguments": [], "environment": {"SDL_AUDIODRIVER": "alsa"}})
        status, data, _ = self.post("/api/launcher/" + GID + "/profile", {"action": "create", "name": "silent", "copy_from": "default"})
        self.assertEqual((status, sorted(data["profiles"])), (200, ["default", "silent"]))
        self.post("/api/launcher/" + GID + "/settings", {"profile": "silent", "arguments": ["-m", "null"], "environment": {"SDL_AUDIODRIVER": "dummy"}})
        job = self.post("/api/launch", {"game": GID, "profile": "silent"})[1]
        self.wait_job(job["id"])
        self.assertEqual(self.out.read_text().splitlines(), ["dummy", "-m", "null"])
        self.assertEqual(self.post("/api/launcher/" + GID + "/profile", {"action": "default", "name": "silent"})[1]["default_profile"], "silent")
        job = self.post("/api/launch", {"game": GID})[1]
        self.wait_job(job["id"])
        self.assertEqual(self.out.read_text().splitlines()[0], "dummy")  # the default profile applies
        self.assertEqual(self.post("/api/launcher/" + GID + "/profile", {"action": "delete", "name": "default"})[0], 400)
        self.assertEqual(self.post("/api/launcher/" + GID + "/profile", {"action": "delete", "name": "silent"})[1]["default_profile"], "default")
        for bad in ({"action": "rename", "name": "x"}, {"action": "create"}, {"action": "create", "name": "bad/name"}, {"action": "default", "name": "ghost"}):
            self.assertEqual(self.post("/api/launcher/" + GID + "/profile", bad)[0], 400, bad)
        self.assertEqual(self.post("/api/launch", {"game": GID, "profile": "ghost"})[0], 409)

    def test_authentication_and_csrf_still_apply(self):
        for method, path, body in (("GET", "/api/launcher/" + GID, None), ("POST", "/api/launcher/" + GID + "/settings", {"arguments": [], "environment": {}}),
                                   ("POST", "/api/launcher/" + GID + "/profile", {"action": "create", "name": "x"})):
            self.assertEqual(self.request(method, path, body, auth=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/launcher/" + GID + "/settings", {"arguments": ["-q"], "environment": {}}, csrf=False)[0], 403)
        self.assertEqual(self.stored()["arguments"], [])
        self.assertEqual(self.request("GET", "/api/launcher/not-an-id")[0], 400)
        self.assertEqual(self.request("GET", "/api/launcher/local:nothing")[0], 404)

    def test_no_execution_surface_was_added(self):
        for path in ("/api/launcher/" + GID + "/exec", "/api/launcher/" + GID + "/run", "/api/launcher/" + GID + "/executable"):
            self.assertIn(self.post(path, {"cmd": "id", "executable": "/bin/sh"})[0], (404, 409))
        self.assertEqual(self.stored()["executable"], self.exe)
