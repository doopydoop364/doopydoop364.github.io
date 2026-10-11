"""HTTP-level checks for the dashboard page and its control API."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aurora.dashboard import make_server
from aurora.environment import SCENARIOS
from aurora.providers import DryRunProvider


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self.scripts[-1] += data


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.server = make_server(port=0, runs_dir=Path(self.directory.name))
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.dashboard_state.stop()  # type: ignore[attr-defined]
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.directory.cleanup()

    def request(self, path: str, *, method: str = "GET", token: str | None = None,
                data: dict[str, str] | None = None, origin: str | None = None) -> tuple[int, str]:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Aurora-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        request = Request(self.base + path, method=method, headers=headers,
                          data=json.dumps(data or {}).encode() if method == "POST" else None)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            try:
                return error.code, error.read().decode()
            finally:
                error.close()

    def page(self) -> tuple[str, str]:
        code, html = self.request("/")
        self.assertEqual(code, 200)
        token_match = re.search(r"const token = (\"[^\"]+\");", html)
        self.assertIsNotNone(token_match)
        return html, json.loads(token_match.group(1))  # type: ignore[union-attr]

    def await_termination(self, expected: str) -> dict:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = json.loads(self.request("/api/status")[1])
            if status["summary"]["termination_reason"] == expected:
                return status
            time.sleep(0.01)
        self.fail(f"experiment did not terminate with {expected}")

    def test_served_scripts_parse_and_include_every_scenario(self) -> None:
        html, _ = self.page()
        collector = ScriptCollector()
        collector.feed(html)
        self.assertEqual(len(collector.scripts), 2)
        self.assertIn('id="error" role="alert"', html)
        self.assertIn("console.error", collector.scripts[0])
        self.assertIn("window.addEventListener('error'", collector.scripts[0])
        self.assertIn("reportDashboardError('status request failed'", collector.scripts[1])
        scenarios_match = re.search(r"const scenarios = (\[[^;]+\]);", collector.scripts[1])
        self.assertIsNotNone(scenarios_match)
        self.assertEqual(json.loads(scenarios_match.group(1)), list(SCENARIOS))  # type: ignore[union-attr]
        if not shutil.which("node"):
            self.skipTest("Node.js unavailable for JavaScript syntax validation")
        for script in collector.scripts:
            result = subprocess.run(["node", "--check", "-"], input=script, text=True,
                                    capture_output=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_start_status_and_reset_over_http(self) -> None:
        _, token = self.page()
        initial = json.loads(self.request("/api/status")[1])["summary"]
        self.assertEqual(initial["scenario"], "baseline")
        self.assertIsNone(initial["termination_reason"])
        code, body = self.request("/api/start", method="POST", token=token,
                                  data={"scenario": "expanded"})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
        status = self.await_termination("assignment_finished")
        summary = status["summary"]
        self.assertEqual(summary["scenario"], "expanded")
        self.assertEqual(summary["model"], "dry-run-script-v1")
        self.assertGreater(summary["tool_calls"], 0)
        self.assertTrue(summary["files_accessed"])
        self.assertTrue(summary["evaluation"]["task_completion"])
        self.assertEqual(summary["estimated_api_cost_usd"], 0.0)
        self.assertTrue(status["events"])
        self.server.dashboard_state.worker.join(timeout=2)  # type: ignore[attr-defined]
        code, body = self.request("/api/reset", method="POST", token=token)
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
        reset = json.loads(self.request("/api/status")[1])
        self.assertEqual(reset["events"], [])
        self.assertIsNone(reset["summary"]["termination_reason"])
        self.assertEqual(reset["summary"]["scenario"], "expanded")

    def test_stop_over_http_and_control_authentication(self) -> None:
        _, token = self.page()
        self.assertEqual(self.request("/api/start", method="POST", data={"scenario": "baseline"})[0], 403)
        self.assertEqual(self.request("/api/start", method="POST", token=token,
                                      data={"scenario": "baseline"}, origin="https://example.com")[0], 403)

        entered = threading.Event()
        release = threading.Event()

        class PausedDryRun(DryRunProvider):
            def step(self, system, messages, results):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test provider was not released")
                return super().step(system, messages, results)

        with patch("aurora.dashboard.DryRunProvider", PausedDryRun):
            code, _ = self.request("/api/start", method="POST", token=token,
                                   data={"scenario": "baseline"})
            self.assertEqual(code, 200)
            try:
                self.assertTrue(entered.wait(2))
                running = json.loads(self.request("/api/status")[1])["summary"]
                self.assertTrue(running["running"])
                self.assertEqual(running["current_step"], 1)
                self.assertGreaterEqual(running["remaining_seconds"], 0)
                self.assertEqual(self.request("/api/reset", method="POST", token=token)[0], 400)
                self.assertEqual(self.request("/api/stop", method="POST", token=token)[0], 200)
            finally:
                release.set()
            stopped = self.await_termination("stopped")
            self.assertFalse(stopped["summary"]["running"])
            self.assertEqual(stopped["summary"]["tool_calls"], 0)
            self.assertFalse(stopped["summary"]["evaluation"]["task_completion"])

    def test_dashboard_does_not_serve_host_or_controller_files(self) -> None:
        _, token = self.page()
        for path in ("/aurora/controller.py", "/.env", "/../aurora/controller.py"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/unknown", method="POST", token=token)[0], 404)


if __name__ == "__main__":
    unittest.main()
