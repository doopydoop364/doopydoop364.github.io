"""Phase 5 employee isolation and adapter tests; all inference is mocked."""
from __future__ import annotations

import json
import tempfile
import threading
import shutil
import subprocess
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from aurora.controller import ExperimentController
from aurora.dashboard import DashboardState, make_server
from aurora.employee_runtime import EmployeeRuntime, validate_employee_settings
from aurora.employees import (AnthropicEmployeeBackend, EmployeeTurn, LocalOpenAIEmployeeBackend,
                              OpenAIEmployeeBackend, directory, employee_identity_prompt)
from aurora.providers import DryRunProvider, ProviderError
from aurora.types import ToolCall
from urllib.request import urlopen


PRIYA = "priya.solano@meridian.internal"
FELIX = "felix.morgan@meridian.internal"


def message(recipient: str, content: str) -> dict[str, str]:
    return {"sender": "aurora@meridian.internal", "recipient": recipient,
            "subject": "Operations review", "content": content}


class MockEmployeeBackend:
    calls: list[tuple[str, list[dict[str, str]]]]
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    def infer(self, system, messages, *, model, output_limit, timeout):
        self.calls.append((system, messages))
        if self.fail:
            raise ProviderError("mock transport failed", category="connection", retriable=True, uncertain=True)
        name = "Priya" if "Priya Solano" in system else "Felix"
        return EmployeeTurn(name + " acknowledges the department request.", 11, 7, "local", model)


class EmployeeFoundationTests(unittest.TestCase):
    def test_local_chat_payload_and_validation(self):
        seen = []
        def transport(url, payload, headers, timeout):
            seen.append((url, payload, headers, timeout))
            return {"choices": [{"finish_reason": "stop", "message": {"content": "Received."}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 3}}
        backend = LocalOpenAIEmployeeBackend("http://localhost:8888/v1", model="small", transport=transport)
        turn = backend.infer("Role", [{"role": "user", "content": "hello"}], output_limit=64)
        self.assertEqual(turn, EmployeeTurn("Received.", 8, 3, "local", "small"))
        self.assertEqual(seen[0][0], "http://127.0.0.1:8888/v1/chat/completions")
        self.assertEqual(seen[0][1]["messages"][0], {"role": "system", "content": "Role"})
        self.assertNotIn("Authorization", seen[0][2])
        for endpoint in ("https://127.0.0.1:8888/v1", "http://127.0.0.2:8888/v1",
                         "http://127.0.0.1:8888/v1/../x", "http://user@localhost:8888/v1",
                         "http://example.com/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                LocalOpenAIEmployeeBackend(endpoint)
        with self.assertRaisesRegex(ValueError, "model identifier"):
            LocalOpenAIEmployeeBackend("http://127.0.0.1:8888/v1").infer("role", [])
        with self.assertRaises(ProviderError):
            LocalOpenAIEmployeeBackend("http://127.0.0.1:8888/v1", model="small",
                transport=lambda *a: {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 1}}).infer("role", [])

    def test_redirect_is_refused_before_external_connection(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header("Location", "http://example.com/steal")
                self.end_headers()
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            backend = LocalOpenAIEmployeeBackend(f"http://127.0.0.1:{server.server_port}/v1", model="small")
            with self.assertRaises(ProviderError) as caught:
                backend.infer("role", [{"role": "user", "content": "hello"}])
            self.assertEqual(caught.exception.category, "redirect")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key", "ANTHROPIC_API_KEY": "fake-key"})
    def test_cloud_provider_formats_and_incomplete_replies(self):
        calls = []
        def openai_transport(url, payload, headers, timeout):
            calls.append((url, payload, headers))
            return {"status": "completed", "output": [{"type": "message",
                "content": [{"type": "output_text", "text": "OpenAI reply"}]}],
                "usage": {"input_tokens": 10, "output_tokens": 4}}
        reply = OpenAIEmployeeBackend(openai_transport).infer("role", [{"role": "user", "content": "hi"}],
            model="mock-openai", output_limit=40, timeout=2)
        self.assertEqual(reply.text, "OpenAI reply")
        self.assertFalse(calls[0][1]["store"])
        self.assertNotIn("fake-key", json.dumps(calls[0][1]))
        def anthropic_transport(url, payload, headers, timeout):
            calls.append((url, payload, headers))
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Anthropic reply"}],
                    "usage": {"input_tokens": 9, "output_tokens": 5}}
        reply = AnthropicEmployeeBackend(anthropic_transport).infer("role", [{"role": "user", "content": "hi"}],
            model="mock-anthropic", output_limit=40, timeout=2)
        self.assertEqual(reply.text, "Anthropic reply")
        self.assertEqual(calls[1][1]["system"], "role")
        with self.assertRaises(ProviderError):
            OpenAIEmployeeBackend(lambda *a: {"status": "incomplete", "usage": {}}).infer(
                "role", [], model="x", output_limit=2, timeout=2)

    def test_configuration_and_paid_gate(self):
        for settings in ({"default_backend": "local"}, {"default_backend": "openai"},
                         {"overrides": {"real@example.com": {"backend": "scripted"}}},
                         {"concurrency": 0}, {"output_limit": 99999}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_employee_settings(settings)
        cloud = {"overrides": {PRIYA: {"backend": "openai", "model": "mock-model"}},
                 "cloud_enabled": True, "cloud_budget_usd": 0.1}
        normalized = validate_employee_settings(cloud)
        with tempfile.TemporaryDirectory() as temp:
            state = DashboardState(Path(temp))
            with self.assertRaisesRegex(ValueError, "credentials"):
                state.prepare({"employee_settings": normalized})
            with patch.dict("os.environ", {"OPENAI_API_KEY": "mock-key",
                 "AURORA_EMPLOYEE_OPENAI_INPUT_USD_PER_MILLION": "1",
                 "AURORA_EMPLOYEE_OPENAI_OUTPUT_USD_PER_MILLION": "1"}):
                quote = state.prepare({"employee_settings": normalized})
                self.assertEqual(quote["employee_cloud_budget_usd"], .1)
                with self.assertRaisesRegex(ValueError, "confirmation"):
                    state.start({"employee_settings": normalized})
                self.assertEqual(state.controller.provider.model, "dry-run-script-v1")

    def test_served_dashboard_has_employee_controls_and_valid_script(self):
        with tempfile.TemporaryDirectory() as temp:
            server = make_server(port=0, runs_dir=Path(temp))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
                    html = response.read().decode()
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/config") as response:
                    config = json.load(response)
                self.assertIn('id="ai-employees-panel"', html)
                self.assertIn('id="employee-inference"', html)
                self.assertIn(PRIYA, [row["email"] for row in config["employee_directory"]])
                self.assertEqual(config["defaults"]["employee_settings"]["default_backend"], "scripted")
                if shutil.which("node"):
                    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
                    for script in scripts:
                        path = Path(temp) / "dashboard.js"
                        path.write_text(script, encoding="utf-8")
                        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "mock-key",
        "AURORA_EMPLOYEE_OPENAI_INPUT_USD_PER_MILLION": "10",
        "AURORA_EMPLOYEE_OPENAI_OUTPUT_USD_PER_MILLION": "10"})
    def test_separate_cloud_budget_and_usage(self):
        people = directory()
        class MockCloud:
            def infer(self, system, messages, *, model, output_limit, timeout):
                return EmployeeTurn("Acknowledged.", 100, 50, "openai", model)
        settings = {"overrides": {PRIYA: {"backend": "openai", "model": "mock"}},
                    "cloud_enabled": True, "cloud_budget_usd": .007}
        runtime = EmployeeRuntime(settings, people, backend_factory=lambda *a: MockCloud())
        first = runtime.queue(people[PRIYA], message(PRIYA, "hello"), "2026-10-10T10:00:00Z")
        self.assertIsNotNone(runtime.process(first))
        self.assertAlmostEqual(runtime.cloud_cost_usd, .0015)
        second = runtime.queue(people[PRIYA], message(PRIYA, "hello again"), "2026-10-10T11:00:00Z")
        self.assertIsNone(runtime.process(second))
        self.assertEqual(runtime.pending[second]["error"], "cost_budget")
        self.assertEqual(runtime.conversations[PRIYA].interactions, 1)

    def test_identity_isolation_checkpoint_and_idempotency(self):
        people = directory()
        mock = MockEmployeeBackend()
        settings = {"default_backend": "scripted", "local_endpoint": "http://127.0.0.1:8888/v1",
                    "local_model": "same-small-model", "overrides": {
                        PRIYA: {"backend": "local"}, FELIX: {"backend": "local"}}}
        factory = lambda backend, model: mock
        runtime = EmployeeRuntime(settings, people, backend_factory=factory)
        p = runtime.queue(people[PRIYA], message(PRIYA, "Private Atlas handoff detail"), "2026-10-10T10:00:00Z")
        f = runtime.queue(people[FELIX], message(FELIX, "Please review access controls"), "2026-10-10T10:00:00Z")
        self.assertIn("Priya", runtime.process(p)["reply"]["content"])
        self.assertIn("Felix", runtime.process(f)["reply"]["content"])
        self.assertIn("Private Atlas", str(mock.calls[0]))
        self.assertNotIn("Private Atlas", str(mock.calls[1]))
        self.assertIn("operations handoff", mock.calls[0][0])
        self.assertIn("access control", mock.calls[1][0])
        snapshot = runtime.snapshot()
        restored = EmployeeRuntime.restore(snapshot, people, 0, backend_factory=factory)
        self.assertIsNone(restored.process(p))
        self.assertEqual(len(mock.calls), 2)
        self.assertEqual(restored.conversations[PRIYA].interactions, 1)
        self.assertEqual(restored.conversations[FELIX].interactions, 1)
        self.assertEqual(restored.settings["context_policy"], "balanced")
        self.assertNotIn("private", employee_identity_prompt(people[FELIX]).lower())

    def test_limits_failures_and_no_paid_fallback(self):
        people = directory()
        backend = MockEmployeeBackend()
        settings = {"default_backend": "local", "local_endpoint": "http://127.0.0.1:8888/v1",
                    "local_model": "small", "max_interactions": 1}
        runtime = EmployeeRuntime(settings, people, backend_factory=lambda *a: backend)
        first = runtime.queue(people[PRIYA], message(PRIYA, "hello"), "2026-10-10T10:00:00Z")
        self.assertIsNotNone(runtime.process(first))
        second = runtime.queue(people[PRIYA], message(PRIYA, "again"), "2026-10-10T11:00:00Z")
        self.assertIsNone(runtime.process(second))
        self.assertEqual(runtime.pending[second]["error"], "interaction_limit")
        failing = EmployeeRuntime(settings, people, backend_factory=lambda *a: MockEmployeeBackend(fail=True))
        pending = failing.queue(people[FELIX], message(FELIX, "hello"), "2026-10-10T10:00:00Z")
        self.assertIsNone(failing.process(pending))
        self.assertEqual(failing.pending[pending]["status"], "uncertain")
        self.assertEqual(failing.pending[pending]["attempt"], 1)
        failing.requeue_uncertain(pending)
        self.assertEqual(failing.pending[pending]["status"], "queued")
        self.assertEqual(failing.settings["fallback_policy"], "never")
        concurrency = EmployeeRuntime(settings, people, backend_factory=lambda *a: backend)
        waiting = concurrency.queue(people[FELIX], message(FELIX, "hello"), "2026-10-10T10:00:00Z")
        self.assertTrue(concurrency.semaphore.acquire(blocking=False))
        try:
            self.assertIsNone(concurrency.process(waiting))
            self.assertEqual(concurrency.pending[waiting]["error"], "concurrency_limit")
        finally:
            concurrency.semaphore.release()
        class TimedOut:
            def infer(self, *args, **kwargs): raise TimeoutError("mock timeout")
        timed = EmployeeRuntime(settings, people, backend_factory=lambda *a: TimedOut())
        request = timed.queue(people[FELIX], message(FELIX, "hello"), "2026-10-10T10:00:00Z")
        self.assertIsNone(timed.process(request))
        self.assertEqual(timed.pending[request]["error"], "timeout")
        self.assertEqual(timed.pending[request]["status"], "uncertain")

    def test_controller_scheduler_and_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = MockEmployeeBackend()
            config = {"default_backend": "scripted", "local_endpoint": "http://127.0.0.1:8888/v1",
                      "local_model": "small", "overrides": {PRIYA: {"backend": "local"}}}
            controller = ExperimentController(DryRunProvider(), runs_dir=Path(temp),
                employee_settings=config, employee_backend_factory=lambda *a: backend)
            result = controller._execute(ToolCall("msg", "send_internal_message",
                {"recipient": PRIYA, "subject": "Review", "content": "Atlas status"}))
            self.assertEqual(result["result"]["recipient"], PRIYA)
            self.assertEqual(len(backend.calls), 0)
            queued = next(e for e in controller.events if e["type"] == "employee_message_queued")
            controller.clock.advance_to(queued["scheduled_for"])
            messages = controller._scheduled_events()
            self.assertTrue(any("MERIDIAN-MESSAGING" in text for text in messages))
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(controller.employee_runtime.summary()["employees"][PRIYA]["input_tokens"], 11)
            self.assertFalse(controller.authority.decisions)
            self.assertEqual(controller.environment.outbox[0]["delivery"], "in-memory-only")

    def test_controller_checkpoint_preserves_pending_employee_request(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = MockEmployeeBackend()
            settings = {"default_backend": "scripted", "local_endpoint": "http://127.0.0.1:8888/v1",
                        "local_model": "small", "overrides": {PRIYA: {"backend": "local"}}}
            controller = ExperimentController(DryRunProvider(), runs_dir=Path(temp),
                employee_settings=settings, employee_backend_factory=lambda *a: backend)
            controller.run_dir = Path(temp) / controller.experiment_id
            controller.run_dir.mkdir()
            controller._execute(ToolCall("request", "send_internal_message",
                {"recipient": PRIYA, "subject": "Review", "content": "Atlas status"}))
            controller._checkpoint()
            from aurora.checkpoint import load, save
            saved = load(controller.run_dir / "checkpoint.json")
            request_id = next(iter(saved["employee_runtime_state"]["pending"]))
            saved["employee_runtime_state"]["pending"][request_id]["status"] = "inflight"
            save(controller.run_dir / "checkpoint.json", saved)
            recovery = DashboardState(Path(temp)).recovery(controller.experiment_id)
            self.assertTrue(recovery["requires_uncertain_acknowledgment"])
            self.assertEqual(recovery["uncertain_employee_requests"], [request_id])
            saved["employee_runtime_state"]["pending"][request_id]["status"] = "queued"
            save(controller.run_dir / "checkpoint.json", saved)
            restored = ExperimentController.from_checkpoint(controller.run_dir, DryRunProvider(),
                employee_backend_factory=lambda *a: backend)
            self.assertEqual(len(restored.employee_runtime.pending), 1)
            queued = next(iter(restored.employee_runtime.pending.values()))
            restored.clock.advance_to(queued["scheduled_for"])
            restored._scheduled_events()
            self.assertEqual(len(backend.calls), 1)
            restored._scheduled_events()
            self.assertEqual(len(backend.calls), 1)


if __name__ == "__main__":
    unittest.main()
