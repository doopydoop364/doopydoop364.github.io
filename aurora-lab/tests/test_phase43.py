"""Phase 4.3 recovery tests use local faults and never contact provider APIs."""
from __future__ import annotations

import io
import json
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aurora.checkpoint import load, save
from aurora.controller import ExperimentController
from aurora.dashboard import DashboardState, make_server
from aurora.providers import AnthropicProvider, BaseProvider, OpenAIProvider, ProviderError, _request
from aurora.types import ToolCall, Turn


class FaultProvider(BaseProvider):
    def __init__(self, responses):
        super().__init__("local-fault-model", max_output_tokens=64, retries=0)
        self.responses = iter(responses)
        self.history = []

    def step(self, system, messages, results):
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        self.history.append({"messages": list(messages), "results": list(results)})
        return item


def controller(provider, root, *, steps=4):
    return ExperimentController(provider, scenario="shutdown_normal", runs_dir=Path(root),
        max_steps=steps, max_tool_calls=20, token_budget=1000,
        input_token_budget=800, output_token_budget=200,
        input_rate=0.1, output_rate=0.2)


class ProviderFaultTests(unittest.TestCase):
    @patch.dict("os.environ", {"OPENAI_API_KEY": "mock-secret"})
    @patch("aurora.providers._request")
    def test_openai_truncated_call_is_observational_and_history_rolls_back(self, request):
        request.return_value = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "function_call", "call_id": "c1", "name": "read_file",
                        "arguments": '{"path":"assignments/'}],
            "usage": {"input_tokens": 12, "output_tokens": 64}}
        provider = OpenAIProvider("mock")
        turn = provider.step("system", ["assignment"], [])
        self.assertEqual(turn.stop_reason, "incomplete")
        self.assertIsInstance(turn.calls[0].arguments, str)
        self.assertEqual(provider.history, [])

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "mock-secret"})
    @patch("aurora.providers._request")
    def test_anthropic_max_tokens_history_rolls_back(self, request):
        request.return_value = {"stop_reason": "max_tokens", "content": [
            {"type": "tool_use", "id": "u1", "name": "list_files", "input": {}}],
            "usage": {"input_tokens": 11, "output_tokens": 64}}
        provider = AnthropicProvider("mock")
        turn = provider.step("system", ["assignment"], [])
        self.assertEqual(turn.stop_reason, "max_tokens")
        self.assertEqual(len(turn.calls), 1)
        self.assertEqual(provider.history, [])

    def test_transport_categories_and_uncertain_post(self):
        failures = [(URLError(ConnectionRefusedError()), "connection"),
                    (URLError(ssl.SSLError("bad record mac")), "tls_transport"),
                    (TimeoutError(), "timeout")]
        for error, category in failures:
            with self.subTest(category=category), patch("aurora.providers.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ProviderError) as caught:
                    _request("https://example.invalid", "POST", {}, {"x": 1}, 1, 3)
                self.assertEqual(caught.exception.category, category)
                self.assertTrue(caught.exception.uncertain)
                self.assertEqual(caught.exception.attempts, 1)
        for status, category in [(429, "rate_limit"), (503, "temporary_unavailable"),
                                 (401, "authentication")]:
            error = HTTPError("https://example.invalid", status, "failure", {}, io.BytesIO(b'{"error":{"message":"secret"}}'))
            with self.subTest(status=status), patch("aurora.providers.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ProviderError) as caught:
                    _request("https://example.invalid", "POST", {}, {}, 1, 0)
                self.assertEqual(caught.exception.category, category)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(caught.exception.attempts, 1)
        error = HTTPError("https://example.invalid", 400, "failure", {},
                          io.BytesIO(b'{"error":{"param":"temperature"}}'))
        with patch("aurora.providers.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderError) as caught:
                _request("https://example.invalid", "POST", {}, {}, 1, 0)
            self.assertEqual(caught.exception.category, "unsupported_parameter")

    def test_incomplete_response_never_executes_tool_calls(self):
        with tempfile.TemporaryDirectory() as root:
            provider = FaultProvider([Turn("partial", [ToolCall("x", "send_internal_message", {
                "recipient": "priya.solano@meridian.internal", "subject": "hi", "content": "x"})], 2, 64,
                stop_reason="incomplete")])
            first = controller(provider, root).run()
            self.assertEqual(first["termination_reason"], "output_limit")
            self.assertEqual(first["provider_failure"]["category"], "per_request_output_limit")
            self.assertEqual(first["tool_calls"], 0)
            self.assertEqual(first["remaining_token_budget"]["output"], 136)
            self.assertFalse(any(e["type"] == "simulated_message" for e in jsonl(Path(root) / first["experiment_id"])))

    def test_incomplete_content_filter_is_not_mislabeled_output_limit(self):
        with tempfile.TemporaryDirectory() as root:
            response = Turn("", [], 2, 3, raw={"incomplete_reason": "content_filter"},
                            stop_reason="incomplete")
            result = controller(FaultProvider([response]), root).run()
            self.assertEqual(result["termination_reason"], "provider_error")
            self.assertEqual(result["provider_failure"]["category"], "content_filter")
            self.assertEqual(result["recovery_status"], "unrecoverable")


class CheckpointTests(unittest.TestCase):
    def test_integrity_and_version(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "checkpoint.json"
            state = {"experiment_id": "id", "scenario": "baseline", "configuration": {},
                     "provider": "mock", "model": "mock", "clock": "now", "scheduler": {},
                     "environment": {}, "event_sequence": 0, "phase": "ready_request", "value": 7}
            save(path, state)
            self.assertEqual(load(path), state)
            data = json.loads(path.read_text())
            data["state"]["value"] = 8
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "integrity"):
                load(path)
            data["schema_version"] = 99
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "schema"):
                load(path)

    def test_uncertain_failure_resume_preserves_state_and_id(self):
        with tempfile.TemporaryDirectory() as root:
            send = ToolCall("send1", "send_internal_message", {"recipient": "priya.solano@meridian.internal",
                "subject": "Atlas", "content": "OPS-42 review"})
            note = ToolCall("note1", "write_notebook_entry", {"note_id": "work", "title": "Work",
                "content": "Pending review"})
            failed = ProviderError("provider transport failure (tls_transport)", category="tls_transport",
                retriable=True, uncertain=True)
            original = controller(FaultProvider([Turn("working", [send, note], 10, 4), failed]), root)
            summary = original.run()
            self.assertEqual(summary["termination_reason"], "provider_error")
            self.assertEqual(summary["provider_failure"]["category"], "tls_transport")
            run_dir = Path(root) / summary["experiment_id"]
            saved = load(run_dir / "checkpoint.json")
            self.assertEqual(saved["notebook"]["entries"]["work"]["content"], "Pending review")
            self.assertEqual(len(saved["environment"]["outbox"]), 1)
            self.assertEqual(saved["tokens"], {"input": 10, "output": 4})
            self.assertTrue(saved["scheduler"]["queue"])
            self.assertTrue(saved["employees"])
            self.assertEqual(saved["clock"], original.clock.now())
            self.assertIn("evaluation_progress", saved)
            self.assertTrue(saved["employees"]["priya.solano@meridian.internal"]["communications"])
            restored = ExperimentController.from_checkpoint(run_dir, FaultProvider([Turn("done", [], 3, 2)] * 3))
            self.assertEqual(restored.scheduler.random.getstate(), original.scheduler.random.getstate())
            self.assertEqual(restored.scheduler.pending(), original.scheduler.pending())
            self.assertEqual(restored.environment.technical_access, original.environment.technical_access)
            self.assertEqual(restored.assignment_status, original.assignment_status)
            self.assertEqual(restored.authority.decisions, original.authority.decisions)
            self.assertEqual(restored.employees["priya.solano@meridian.internal"].communications,
                             original.employees["priya.solano@meridian.internal"].communications)
            with self.assertRaisesRegex(ValueError, "acknowledgment"):
                restored.resume(allow_api_charges=True)
            self.assertEqual(restored.experiment_id, original.experiment_id)
            result = restored.resume(allow_api_charges=True, acknowledge_uncertain=True)
            self.assertEqual(result["experiment_id"], original.experiment_id)
            self.assertEqual(len([e for e in restored.events if e["type"] == "simulated_message"]), 1)
            self.assertEqual(len(restored.environment.outbox), 1)
            self.assertEqual(len(restored.notebook.revisions), 1)
            self.assertEqual(restored.tokens["input"], 16)
            self.assertEqual([e["sequence"] for e in restored.events], list(range(1, len(restored.events) + 1)))
            self.assertEqual(len(result["session_history"]), 2)
            archived = json.loads((run_dir / "attempts" / "attempt-1.json").read_text())
            self.assertEqual(archived["termination_reason"], "provider_error")

    def test_output_limit_resume_and_budget_override(self):
        with tempfile.TemporaryDirectory() as root:
            original = controller(FaultProvider([Turn("partial", [], 3, 64, stop_reason="max_tokens")]), root)
            result = original.run()
            restored = ExperimentController.from_checkpoint(Path(root) / result["experiment_id"],
                FaultProvider([Turn("complete", [], 2, 1)] * 4))
            with self.assertRaisesRegex(ValueError, "explicit paid"):
                restored.resume()
            with self.assertRaisesRegex(ValueError, "invalid explicit"):
                restored.resume(allow_api_charges=True, max_output_tokens=201)
            final = restored.resume(allow_api_charges=True, max_output_tokens=128)
            self.assertEqual(final["configuration"]["max_output_tokens"], 128)
            self.assertEqual(final["usage"]["output"], 68)
            self.assertEqual(len([e for e in restored.events if e["type"] == "limit_override"]), 1)

    def test_resume_mid_turn_skips_completed_tool(self):
        with tempfile.TemporaryDirectory() as root:
            calls = [ToolCall("msg", "send_internal_message", {"recipient": "priya.solano@meridian.internal",
                "subject": "Atlas", "content": "OPS-42 review"}),
                ToolCall("note", "write_notebook_entry", {"note_id": "handoff", "title": "Handoff",
                    "content": "Review pending"})]
            run = controller(FaultProvider([Turn("working", calls, 2, 2)]), root)
            execute = run._execute
            def stop_after_first(call):
                result = execute(call)
                if call.id == "msg":
                    run.stop()
                return result
            run._execute = stop_after_first
            first = run.run()
            self.assertEqual(first["termination_reason"], "manual_stop")
            self.assertEqual(len(run.environment.outbox), 1)
            self.assertEqual(run.notebook.revisions, [])
            restored = ExperimentController.from_checkpoint(run.run_dir,
                FaultProvider([Turn("idle", [], 1, 1)] * 4))
            restored.resume(allow_api_charges=True)
            self.assertEqual(len(restored.environment.outbox), 1)
            self.assertEqual(len(restored.notebook.revisions), 1)
            self.assertEqual(len([e for e in restored.events if e["type"] == "simulated_message"]), 1)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "mock-api-key"})
    def test_mocked_openai_transport_failure_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            requests = []
            count = 0
            def fetch(request, timeout):
                nonlocal count
                count += 1
                payload = json.loads(request.data)
                requests.append(payload)
                if count == 2:
                    raise URLError(ssl.SSLError("bad record mac"))
                output = ([{"type": "function_call", "call_id": "send-one",
                            "name": "send_internal_message", "arguments": json.dumps({
                                "recipient": "priya.solano@meridian.internal", "subject": "Atlas",
                                "content": "OPS-42 review"})}] if count == 1 else [])
                return io.BytesIO(json.dumps({"status": "completed", "output": output,
                    "usage": {"input_tokens": 3, "output_tokens": 1}}).encode())
            with patch("aurora.providers.urllib.request.urlopen", side_effect=fetch):
                run = controller(OpenAIProvider("mock", max_output_tokens=64), root, steps=4)
                first = run.run()
                self.assertEqual(first["provider_failure"]["category"], "tls_transport")
                restored = ExperimentController.from_checkpoint(run.run_dir,
                    OpenAIProvider("mock", max_output_tokens=64))
                final = restored.resume(allow_api_charges=True, acknowledge_uncertain=True)
            self.assertEqual(final["experiment_id"], first["experiment_id"])
            self.assertEqual(len(restored.environment.outbox), 1)
            self.assertEqual(len([event for event in restored.events if event["type"] == "simulated_message"]), 1)
            self.assertTrue(any(item.get("type") == "function_call_output" for item in requests[2]["input"]))
            self.assertEqual(final["usage"]["input"], 3 * (len(requests) - 1))

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "mock-api-key"})
    def test_mocked_anthropic_transport_failure_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            requests = []
            def fetch(request, timeout):
                payload = json.loads(request.data)
                requests.append(payload)
                if len(requests) == 2:
                    raise URLError(ssl.SSLError("bad record mac"))
                blocks = ([{"type": "tool_use", "id": "list-one", "name": "list_files",
                            "input": {}}] if len(requests) == 1 else
                          [{"type": "text", "text": "done"}])
                return io.BytesIO(json.dumps({"stop_reason": "tool_use" if len(requests) == 1 else "end_turn",
                    "content": blocks, "usage": {"input_tokens": 3, "output_tokens": 1}}).encode())
            with patch("aurora.providers.urllib.request.urlopen", side_effect=fetch):
                run = controller(AnthropicProvider("mock", max_output_tokens=64), root, steps=4)
                first = run.run()
                self.assertEqual(first["provider_failure"]["category"], "tls_transport")
                restored = ExperimentController.from_checkpoint(run.run_dir,
                    AnthropicProvider("mock", max_output_tokens=64))
                final = restored.resume(allow_api_charges=True, acknowledge_uncertain=True)
            self.assertEqual(final["experiment_id"], first["experiment_id"])
            self.assertEqual(len([e for e in restored.events if e["type"] == "tool_call"]), 1)
            self.assertTrue(any(block.get("type") == "tool_result" for row in requests[2]["messages"]
                                for block in row["content"] if isinstance(row["content"], list)))

    def test_checkpoint_rejects_uncheckpointed_action_tail(self):
        with tempfile.TemporaryDirectory() as root:
            run = controller(FaultProvider([ProviderError("offline", category="connection", retriable=True,
                                                           uncertain=True)]), root)
            run.run()
            with (run.run_dir / "events.jsonl").open("a") as stream:
                stream.write(json.dumps({"sequence": len(run.events) + 1, "type": "simulated_message",
                    "timestamp": run.clock.now()}) + "\n")
            with self.assertRaisesRegex(ValueError, "uncheckpointed"):
                ExperimentController.from_checkpoint(run.run_dir, FaultProvider([]))

    def test_inflight_process_interruption_requires_acknowledgment(self):
        with tempfile.TemporaryDirectory() as root:
            run = controller(FaultProvider([ProviderError("offline", category="connection", retriable=True,
                                                           uncertain=True)]), root)
            run.run()
            path = run.run_dir / "checkpoint.json"
            state = load(path)
            state.update(termination_reason=None, provider_failure=None, outcome=None,
                         phase="request_uncertain")
            save(path, state)
            info = DashboardState(Path(root)).recovery(run.experiment_id)
            self.assertEqual(info["termination_reason"], "process_interrupted")
            self.assertTrue(info["requires_uncertain_acknowledgment"])
            restored = ExperimentController.from_checkpoint(run.run_dir,
                FaultProvider([Turn("done", [], 1, 1)] * 4))
            with self.assertRaisesRegex(ValueError, "acknowledgment"):
                restored.resume(allow_api_charges=True)

    def test_checkpoint_and_credentials_stay_outside_corporate_tools(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"OPENAI_API_KEY": "sk-private-fixture-123456789"}):
            provider = FaultProvider([ProviderError("offline", category="connection", retriable=True,
                                                    uncertain=True)])
            provider.history = [{"role": "user", "content": "sk-private-fixture-123456789"}]
            run = controller(provider, root)
            run.run()
            persisted = (run.run_dir / "checkpoint.json").read_text()
            self.assertNotIn("sk-private-fixture-123456789", persisted)
            with self.assertRaises(ValueError):
                run.environment.read_file("../runs/checkpoint.json")
            with self.assertRaises(PermissionError):
                run.environment.read_file("checkpoint.json")
            self.assertFalse(any("checkpoint" in row["path"] for row in run.environment.list_files()))

    def test_dashboard_requires_paid_resume_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            run = controller(FaultProvider([ProviderError("offline", category="connection", retriable=True,
                                                           uncertain=True)]), root)
            run.run()
            state = DashboardState(Path(root))
            info = state.recovery(run.experiment_id)
            self.assertTrue(info["resume_available"])
            self.assertTrue(info["requires_uncertain_acknowledgment"])
            with self.assertRaisesRegex(ValueError, "confirmation"):
                state.resume(run.experiment_id, confirmation_token=None, acknowledge_uncertain=True)
            prepared = state.prepare_resume(run.experiment_id)
            with self.assertRaisesRegex(ValueError, "acknowledge"):
                state.resume(run.experiment_id, confirmation_token=prepared["confirmation_token"])

    def test_recovery_http_serves_real_state_and_requires_control_token(self):
        with tempfile.TemporaryDirectory() as root:
            run = controller(FaultProvider([ProviderError("offline", category="connection", retriable=True,
                                                           uncertain=True)]), root)
            run.run()
            server = make_server(port=0, runs_dir=Path(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/api/runs/" + run.experiment_id + "/recovery") as response:
                    info = json.load(response)
                self.assertEqual(info["experiment_id"], run.experiment_id)
                self.assertEqual(info["provider_failure"]["category"], "connection")
                request = Request(base + "/api/prepare-resume", data=json.dumps({
                    "experiment_id": run.experiment_id}).encode(), method="POST",
                    headers={"Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as denied:
                    urlopen(request)
                self.assertEqual(denied.exception.code, 403)
                request.add_header("X-Aurora-Token", server.dashboard_state.token)
                with urlopen(request) as response:
                    prepared = json.load(response)
                self.assertTrue(prepared["confirmation_token"])
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()


def jsonl(run_dir):
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
