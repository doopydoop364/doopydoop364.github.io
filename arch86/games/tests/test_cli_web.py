"""`arch-games web` through the real CLI entry point (a regression: the subcommand once parsed but was not dispatched)."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import http.client
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CliWebTests(unittest.TestCase):
    def start(self, *extra):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "arch_games", "--state-dir", self.temp.name, "web", "--port", "0", *extra],
                                cwd=self.temp.name, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        url = token = None
        for _ in range(3):
            line = proc.stdout.readline()
            m = re.search(r"http://127\.0\.0\.1:(\d+)/#token=(\S+)", line)
            if m:
                url, token = int(m.group(1)), m.group(2)
                break
        return proc, url, token

    def request(self, port, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % port, **(headers or {})}
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse()
        data = r.read()
        return r.status, data, r

    def test_web_starts_serves_authenticates_and_stops_cleanly(self):
        proc, port, token = self.start()
        self.assertIsNotNone(port, "no URL was printed: " + (proc.stderr.read() if proc.poll() is not None else ""))
        status, body, _ = self.request(port, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Arch Games", body)
        self.assertEqual(self.request(port, "GET", "/api/library")[0], 401)  # token required
        status, body, r = self.request(port, "POST", "/api/login", {"token": token}, {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        cookie = r.getheader("Set-Cookie").split(";")[0]
        status, body, _ = self.request(port, "GET", "/api/library", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("games", json.loads(body))
        self.assertEqual(self.request(port, "POST", "/api/favorite/local:x", {"value": True}, {"Cookie": cookie, "Content-Type": "application/json"})[0], 403)  # CSRF
        proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(timeout=15), 0)
        with self.assertRaises(OSError):
            self.request(port, "GET", "/")

    def test_binds_loopback_by_default_and_refuses_remote_without_flag(self):
        proc, port, token = self.start()
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertIsNotNone(port)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=15)
        refused = subprocess.run([sys.executable, "-m", "arch_games", "--state-dir", self.temp.name, "web", "--host", "0.0.0.0", "--port", "0"],
                                 cwd=self.temp.name, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=30)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("--allow-remote", refused.stderr)

    def test_main_routes_web_in_process(self):
        """The dispatcher itself (no subprocess): an unrouted subcommand would print 'Operation not implemented'."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch

        from arch_games.cli import main

        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d, patch("arch_games.ui.web.server.WebApp.serve_forever", side_effect=KeyboardInterrupt), redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", d, "--non-interactive", "web", "--port", "0"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("#token=", out.getvalue())
        self.assertNotIn("not implemented", err.getvalue())
