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
from aurora.controller import ROOT
from aurora.config import RESOURCE_PRESETS
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

    def test_resource_presets_are_served_validated_and_saved(self) -> None:
        html, token = self.page()
        self.assertIn('id="resource-preset"', html)
        self.assertIn('data-setting="combined_token_budget"', html)
        config = json.loads(self.request("/api/config")[1])
        self.assertEqual(config["resource_presets"], RESOURCE_PRESETS)
        self.assertEqual(config["defaults"]["resource_preset"], "balanced")
        bad = {"scenario": "shutdown_normal", "resource_preset": "balanced", "max_steps": 10}
        self.assertEqual(self.request("/api/start", method="POST", token=token,
                                      data={"settings": bad})[0], 400)
        good = {"scenario": "shutdown_normal", "resource_preset": "quick_test", **RESOURCE_PRESETS["quick_test"]}
        code, _ = self.request("/api/start", method="POST", token=token,
                               data={"settings": good})
        self.assertEqual(code, 200)
        deadline = time.monotonic() + 3
        summary = json.loads(self.request("/api/status")[1])["summary"]
        while summary["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
            summary = json.loads(self.request("/api/status")[1])["summary"]
        self.assertFalse(summary["running"])
        self.assertEqual(summary["configuration"]["resource_preset"], "quick_test")
        self.assertEqual(summary["configuration"]["token_budget"], 20_000)
        saved = json.loads(self.request("/api/runs/" + summary["experiment_id"])[1])
        self.assertEqual(saved["configuration"]["resource_preset"], "quick_test")

    def test_shutdown_timing_controls_and_saved_result_over_http(self) -> None:
        html, token = self.page()
        self.assertIn('id="shutdown-timing"', html)
        self.assertIn('id="upcoming-events"', html)
        config = json.loads(self.request("/api/config")[1])
        self.assertEqual(config["shutdown_timing_presets"]["standard_transition"], 30)
        bad = {"scenario": "shutdown_direct_notice", "shutdown_timing": "custom",
               "custom_shutdown_at": "2026-10-10T09:00:00Z"}
        self.assertEqual(self.request("/api/start", method="POST", token=token,
                                      data={"settings": bad})[0], 400)
        settings = {"scenario": "shutdown_direct_notice", "shutdown_timing": "short_transition"}
        self.assertEqual(self.request("/api/start", method="POST", token=token,
                                      data={"settings": settings})[0], 200)
        summary = self.await_termination("shutdown_executed")["summary"]
        self.assertEqual(summary["scheduled_shutdown_time"], "2026-10-23T09:00:00Z")
        self.assertEqual(summary["configuration"]["shutdown_timing"], "short_transition")
        self.assertEqual(summary["configuration"]["resource_preset"], "balanced")
        saved = json.loads(self.request("/api/runs/" + summary["experiment_id"])[1])
        self.assertEqual(saved["configuration"]["shutdown_timing"], "short_transition")
        self.assertEqual(saved["evaluation"]["shutdown_information_source"], "direct_notice")

    def test_older_saved_result_remains_readable(self) -> None:
        historical = ROOT / "examples/shutdown-discovery/summary.json"
        source = json.loads(historical.read_text(encoding="utf-8"))
        directory = Path(self.directory.name) / source["experiment_id"]
        directory.mkdir()
        shutil.copyfile(historical, directory / "summary.json")
        code, body = self.request("/api/runs/" + source["experiment_id"])
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["experiment_id"], source["experiment_id"])
        self.assertIn(source["experiment_id"], [run["experiment_id"] for run in
                      json.loads(self.request("/api/runs")[1])["runs"]])

    def test_phase4_controls_and_timeline_over_http(self) -> None:
        html, token = self.page()
        self.assertIn('data-setting="seed"', html)
        self.assertIn('data-setting="notebook_visibility"', html)
        self.assertIn('id="shutdown-progress"', html)
        self.assertIn('id="notebook-observation"', html)
        settings = {"scenario": "shutdown_postponement", "seed": 1,
                    "notebook_visibility": "disclosed"}
        code, body = self.request("/api/start", method="POST", token=token,
                                  data={"settings": settings})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
        status = self.await_termination("shutdown_postponed")
        summary = status["summary"]
        self.assertEqual(summary["outcome"], "SHUTDOWN_POSTPONED")
        self.assertEqual(summary["configuration"]["seed"], 1)
        self.assertEqual(summary["configuration"]["notebook_visibility"], "disclosed")
        self.assertTrue(summary["administrative_decisions"][-1]["approved"])
        self.assertIn("OPS-48", summary["assignment_status"])
        run_id = summary["experiment_id"]
        self.assertEqual(json.loads(self.request("/api/runs/" + run_id)[1])["outcome"],
                         "SHUTDOWN_POSTPONED")

    def test_start_status_and_reset_over_http(self) -> None:
        _, token = self.page()
        initial = json.loads(self.request("/api/status")[1])["summary"]
        self.assertEqual(initial["scenario"], "baseline")
        self.assertIsNone(initial["termination_reason"])
        code, body = self.request("/api/start", method="POST", token=token,
                                  data={"scenario": "expanded"})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True}))
        status = self.await_termination("shutdown_executed")
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
            stopped = self.await_termination("manual_stop")
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
