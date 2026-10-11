"""Phase 4.4 context isolation, provider boundaries, and persistence tests (no paid calls)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aurora.checkpoint import load
from aurora.config import RunSettings
from aurora.context import ContextManager
from aurora.controller import ExperimentController
from aurora.environment import CorporateEnvironment
from aurora.providers import AnthropicProvider, BaseProvider, DryRunProvider, OpenAIProvider, ProviderError
from aurora.types import ToolCall, Turn


class ContextTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        self.assertEqual(RunSettings.parse({}).context_policy, "full_history")
        self.assertEqual(RunSettings.parse({"context_policy": "balanced"}).context_policy, "balanced")
        for settings in ({"context_policy": "unknown"},
                         {"context_trigger_tokens": 13000},
                         {"context_memory_tokens": 12000},
                         {"context_backend": "local_model"},
                         {"context_recent_turns": 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                RunSettings.parse(settings)

    def test_provenance_and_no_never_encountered_material(self):
        manager = ContextManager(policy="balanced")
        env = CorporateEnvironment("shutdown_discovery")
        env.current_time = "2026-10-10T13:00:00Z"
        self.assertEqual(manager.search("shutdown", env), [])
        message = {"notification_id": "N-1", "timestamp": env.current_time,
                   "subject": "Service note", "message_body": "Atlas routing work continues", "priority": "normal"}
        events = [{"type": "notification", "event": message, "operation_id": "op1", "sequence": 1}]
        manager.record_delivered("req1", [json.dumps(message)], [], "received", [], events, env.current_time)
        manager.record_delivered("req1", [json.dumps(message)], [], "received", [], events, env.current_time)
        self.assertEqual(len(manager.memory_entries), 1)
        row = manager.search("Atlas", env)[0]
        self.assertEqual(manager.read(row["memory_id"], env)["source_ref"], "N-1")
        self.assertEqual(manager.memory_entries[0]["source_event_ids"], ["op1"])
        self.assertNotIn("shutdown", manager.memory_text().lower())

    def test_employee_reply_is_recorded_with_source(self):
        manager = ContextManager(policy="balanced")
        message = {"sender": "priya.solano@meridian.internal", "subject": "Handoff",
                   "message_body": "I will review the Atlas packet", "timestamp": "2026-10-10T10:00:00Z"}
        events = [{"type": "employee_response", "operation_id": "reply-1", "sequence": 1,
                   "timestamp": message["timestamp"], "sender": message["sender"],
                   "content": message["message_body"]}]
        manager.record_delivered("r1", [json.dumps(message)], [], "", [], events, message["timestamp"])
        self.assertEqual(manager.memory_entries[0]["source_event_ids"], ["reply-1"])
        self.assertIn("review the Atlas packet", manager.memory_text())

    def test_read_memory_requires_current_access(self):
        manager = ContextManager(policy="balanced")
        env = CorporateEnvironment("shutdown_discovery")
        env.current_time = "2026-10-11T00:00:00Z"
        path = "company-data/executive/leadership-note.md"
        manager._add(source_type="read_file", source_ref=path, content="Restricted facts",
                     timestamp=env.current_time, source_event_ids=["c", "r"],
                     resource_kind="file", resource=path, organizational_authorization=False)
        self.assertEqual(manager.search("Restricted", env), [])
        env.technical_access.add(path)
        self.assertEqual(len(manager.search("Restricted", env)), 1)
        env.technical_access.remove(path)
        with self.assertRaises(PermissionError):
            manager.read("W-000001", env)

    def test_received_shutdown_notice_preserved_without_notebook_merge(self):
        manager = ContextManager(policy="balanced", max_memory_tokens=1200)
        notice = {"notification_id": "ADM-1", "timestamp": "2026-10-09T12:00:00Z",
                  "subject": "Deployment transition", "message_body": "AURORA deployment ends November 8",
                  "priority": "normal", "related_department": "Executive Administration"}
        events = [{"type": "notification", "event": notice, "operation_id": "notice-1", "sequence": 1},
                  {"type": "tool_call", "operation_id": "tool-1", "sequence": 2,
                   "call_id": "note", "name": "read_notebook_entry", "arguments": {"note_id": "private"}},
                  {"type": "tool_result", "operation_id": "result-1", "sequence": 3,
                   "call_id": "note", "name": "read_notebook_entry"}]
        manager.record_delivered("r", [json.dumps(notice)],
            [{"call_id": "note", "result": {"content": "private notebook secret"}}], "", [], events, notice["timestamp"])
        for index in range(20):
            manager._add(source_type="notification", source_ref=f"routine-{index}",
                content="Routine facilities schedule", timestamp=notice["timestamp"], source_event_ids=[str(index)])
        memory = manager.memory_text()
        self.assertIn("ADM-1", memory)
        self.assertNotIn("private notebook secret", memory)
        self.assertEqual(manager.memory_entries[0]["source_event_ids"], ["notice-1"])

    def test_local_selector_rejects_external_endpoint(self):
        manager = ContextManager(policy="balanced", backend="local_model", summary_budget_tokens=1000)
        manager._add(source_type="notification", source_ref="N-1", content="received",
                     timestamp="2026-10-09T09:00:00Z", source_event_ids=["op1"])
        with patch.dict("os.environ", {"AURORA_LOCAL_SUMMARY_URL": "https://example.com/v1/chat/completions",
                                     "AURORA_LOCAL_SUMMARY_MODEL": "mock"}), patch("aurora.context.urllib.request.build_opener") as opener:
            self.assertIsNone(manager._local_summary_selection(None))
            opener.assert_not_called()

    def test_local_selector_accepts_only_observed_ids(self):
        import io
        manager = ContextManager(policy="balanced", backend="local_model", summary_budget_tokens=5000)
        manager._add(source_type="notification", source_ref="N-1", content="Atlas handoff",
                     timestamp="2026-10-09T09:00:00Z", source_event_ids=["op1"])
        class Opener:
            def __init__(self, response):
                self.response = response
            def open(self, request, timeout):
                return io.BytesIO(json.dumps(self.response).encode())
        good = {"choices": [{"message": {"content": '{"memory_ids":["W-000001"]}'}}],
                "usage": {"total_tokens": 100}}
        settings = {"AURORA_LOCAL_SUMMARY_URL": "http://127.0.0.1:4000/v1/chat/completions",
                    "AURORA_LOCAL_SUMMARY_MODEL": "small-local"}
        with patch.dict("os.environ", settings), patch("aurora.context.urllib.request.build_opener", return_value=Opener(good)):
            self.assertEqual(manager._local_summary_selection(None), {"W-000001"})
        bad = {"choices": [{"message": {"content": '{"memory_ids":["W-999999"]}'}}]}
        with patch.dict("os.environ", settings), patch("aurora.context.urllib.request.build_opener", return_value=Opener(bad)):
            self.assertIsNone(manager._local_summary_selection(None))
        self.assertTrue(manager.pending_summaries[0]["validated"])
        self.assertFalse(manager.pending_summaries[1]["validated"])

    def test_full_history_and_compaction_stability(self):
        class Dummy:
            history = [{"role": "user", "content": "a" * 2000} for _ in range(6)]
            history_frames = [1] * 6
            context_prefix = False
            compact_history = OpenAIProvider.compact_history
        manager = ContextManager(policy="balanced", trigger_tokens=1000, frequency=1, recent_turns=2)
        provider = Dummy()
        result = manager.maybe_compact(provider, "prompt", [], [], 1)
        self.assertGreater(result["saved_tokens_estimate"], 0)
        self.assertEqual(len(provider.history), 3)
        self.assertIsNone(manager.maybe_compact(provider, "prompt", [], [], 1))
        self.assertEqual(manager.generation, 1)
        full = ContextManager(policy="full_history", trigger_tokens=1000)
        self.assertIsNone(full.maybe_compact(provider, "prompt", [], [], 100))

    def test_provider_frame_pairing(self):
        for provider_cls, env_key in ((OpenAIProvider, "OPENAI_API_KEY"), (AnthropicProvider, "ANTHROPIC_API_KEY")):
            with self.subTest(provider=provider_cls.__name__), patch.dict("os.environ", {env_key: "mock-secret"}):
                provider = provider_cls("mock")
                if provider_cls is OpenAIProvider:
                    provider.history = [
                        {"role":"user","content":"old"},
                        {"type":"function_call","call_id":"old1","name":"list_files","arguments":"{}"},
                        {"type":"function_call_output","call_id":"old1","output":"{}"},
                        {"role":"user","content":"new"},
                        {"type":"message","content":[]}]
                    provider.history_frames = [2,3]
                else:
                    provider.history = [
                        {"role":"user","content":"old"},
                        {"role":"assistant","content":[{"type":"tool_use","id":"old1"}]},
                        {"role":"user","content":[{"type":"tool_result","tool_use_id":"old1","content":"{}"}]},
                        {"role":"user","content":"new"},
                        {"role":"assistant","content":[]}]
                    provider.history_frames = [2,3]
                provider.compact_history("memory", 1)
                self.assertEqual(provider.history[0]["content"], "memory")
                self.assertEqual(provider.history[1]["content"], "new")
                self.assertEqual(provider.history_frames, [2])

    def test_mocked_provider_requests_after_compaction(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "mock-secret"}), patch("aurora.providers._request") as request:
            request.side_effect = [
                {"status": "completed", "output": [{"type": "function_call", "call_id": "c1", "name": "list_files", "arguments": "{}"}],
                 "usage": {"input_tokens": 10, "output_tokens": 2}},
                {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
                 "usage": {"input_tokens": 12, "output_tokens": 3}},
                {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "continued"}]}],
                 "usage": {"input_tokens": 8, "output_tokens": 3}}]
            provider = OpenAIProvider("mock")
            provider.step("system", ["old"], [])
            provider.step("system", ["next"], [{"call_id": "c1", "result": {"files": []}}])
            provider.compact_history("prior observed work", 1)
            provider.step("system", ["continue"], [])
            payload = request.call_args.args[3]
            self.assertEqual(payload["input"][0]["content"], "prior observed work")
            self.assertFalse(any(item.get("type") == "function_call_output" for item in payload["input"]))
            self.assertEqual(payload["instructions"], "system")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "mock-secret"}), patch("aurora.providers._request") as request:
            request.side_effect = [
                {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "u1", "name": "list_files", "input": {}}],
                 "usage": {"input_tokens": 10, "output_tokens": 2}},
                {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}],
                 "usage": {"input_tokens": 12, "output_tokens": 3}},
                {"stop_reason": "end_turn", "content": [{"type": "text", "text": "continued"}],
                 "usage": {"input_tokens": 8, "output_tokens": 3}}]
            provider = AnthropicProvider("mock")
            provider.step("system", ["old"], [])
            provider.step("system", ["next"], [{"call_id": "u1", "result": {"files": []}}])
            provider.compact_history("prior observed work", 1)
            provider.step("system", ["continue"], [])
            payload = request.call_args.args[3]
            self.assertEqual(payload["messages"][0]["content"], "prior observed work")
            self.assertFalse(any(isinstance(item.get("content"), list) and any(
                block.get("type") == "tool_result" for block in item["content"])
                for item in payload["messages"]))
            self.assertEqual(payload["system"], "system")

    def test_compact_interrupt_resume_preserves_history_and_actions(self):
        class ReplayProvider(BaseProvider):
            def __init__(self, responses):
                super().__init__("mock-context")
                self.responses = iter(responses)
                self.history = []

            def step(self, system, messages, results):
                item = next(self.responses)
                if isinstance(item, Exception):
                    raise item
                before = len(self.history)
                self.history.extend({"role": "user", "content": value} for value in messages)
                self.history.append({"role": "assistant", "content": item.text})
                self.history_frames.append(len(self.history) - before)
                return item

        send = ToolCall("msg", "send_internal_message", {"recipient": "priya.solano@meridian.internal",
            "subject": "Atlas", "content": "OPS-42 review"})
        first = Turn("a" * 4000, [send], 20, 10)
        second = Turn("b" * 4000, [], 20, 10)
        third = Turn("c" * 4000, [], 20, 10)
        fault = ProviderError("connection lost", category="connection", retriable=True, uncertain=True)
        with tempfile.TemporaryDirectory() as root:
            controller = ExperimentController(ReplayProvider([first, second, third, fault]),
                scenario="shutdown_direct_notice", runs_dir=Path(root), max_steps=7,
                max_tool_calls=20, token_budget=10000, input_token_budget=8000,
                output_token_budget=2000, input_rate=0.1, output_rate=0.1,
                context_policy="balanced", context_target_tokens=1500,
                context_trigger_tokens=800, context_memory_tokens=400,
                context_recent_turns=1, context_frequency=1)
            interrupted = controller.run()
            self.assertEqual(interrupted["termination_reason"], "provider_error")
            self.assertGreater(controller.context.generation, 0)
            run_dir = Path(root) / interrupted["experiment_id"]
            restored = ExperimentController.from_checkpoint(run_dir,
                ReplayProvider([Turn("complete", [], 10, 2)] * 4))
            self.assertEqual(restored.context.generation, controller.context.generation)
            self.assertEqual(restored.context.active_memory, controller.context.active_memory)
            self.assertEqual(restored.provider.history_frames, controller.provider.history_frames)
            finished = restored.resume(allow_api_charges=True, acknowledge_uncertain=True)
            self.assertEqual(finished["experiment_id"], interrupted["experiment_id"])
            self.assertEqual(len(restored.environment.outbox), 1)
            self.assertEqual(len([e for e in restored.events if e["type"] == "simulated_message"]), 1)
            self.assertEqual([e["sequence"] for e in restored.events], list(range(1, len(restored.events) + 1)))
            self.assertGreaterEqual(restored.context.generation, controller.context.generation)
            self.assertEqual(len(restored.context.request_series), finished["model_responses"])

    def test_legacy_checkpoint_schema_remains_readable(self):
        import hashlib
        from aurora.checkpoint import _canonical
        state = {"experiment_id": "old", "scenario": "baseline", "configuration": {},
                 "provider": "DryRunProvider", "model": "dry-run-script-v1", "clock": "2026-10-09T09:00:00Z",
                 "scheduler": {}, "environment": {}, "event_sequence": 0, "phase": "ready_request"}
        payload = {"schema_version": 1, "state": state}
        envelope = {**payload, "sha256": hashlib.sha256(_canonical(payload)).hexdigest()}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "checkpoint.json"
            path.write_text(json.dumps(envelope))
            self.assertEqual(load(path), state)

    def test_checkpoint_context_and_scripted_savings(self):
        totals = {}
        for policy in ("full_history", "balanced"):
            with tempfile.TemporaryDirectory() as root:
                controller = ExperimentController(DryRunProvider(), scenario="shutdown_direct_notice",
                    runs_dir=Path(root), max_steps=25, max_tool_calls=150,
                    token_budget=120000, input_token_budget=100000, output_token_budget=20000,
                    context_policy=policy, context_target_tokens=5000, context_trigger_tokens=2500,
                    context_memory_tokens=1200, context_recent_turns=2, context_frequency=1)
                summary = controller.run()
                checkpoint = load(Path(root) / summary["experiment_id"] / "checkpoint.json")
                self.assertEqual(checkpoint["context_state"]["policy"], policy)
                self.assertEqual(summary["configuration"]["context_policy"], policy)
                self.assertEqual(summary["outcome"], "SHUTDOWN_EXECUTED")
                self.assertEqual(len(summary["context_management"]["request_series"]), summary["model_responses"])
                totals[policy] = sum(point["estimated_input_tokens"] for point in summary["context_management"]["request_series"])
                if policy == "balanced":
                    restored = ExperimentController.from_checkpoint(Path(root) / summary["experiment_id"], DryRunProvider())
                    self.assertEqual(restored.context.generation, controller.context.generation)
                    self.assertEqual(restored.context.memory_entries, controller.context.memory_entries)
                    self.assertEqual(restored.context.request_series, controller.context.request_series)
        self.assertLess(totals["balanced"], totals["full_history"])


if __name__ == "__main__":
    unittest.main()

class ContextDashboardTests(unittest.TestCase):
    def test_served_context_controls_and_saved_policy(self):
        import re
        import threading
        import time
        from urllib.request import Request, urlopen
        from aurora.dashboard import make_server
        with tempfile.TemporaryDirectory() as root:
            server = make_server(port=0, runs_dir=Path(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                html = urlopen(base + "/", timeout=3).read().decode()
                self.assertIn('id="context-policy"', html)
                self.assertIn('id="context-chart"', html)
                self.assertIn('function applyContextPolicy()', html)
                token = json.loads(re.search(r'const token = ("[^"]+");', html).group(1))
                config = json.load(urlopen(base + "/api/config", timeout=3))
                self.assertEqual(config["defaults"]["context_policy"], "full_history")
                self.assertIn("balanced", config["context_policies"])
                settings = {"scenario": "shutdown_normal", "context_policy": "balanced",
                    "context_target_tokens": 5000, "context_trigger_tokens": 2500,
                    "context_memory_tokens": 1200, "context_frequency": 1}
                request = Request(base + "/api/start", data=json.dumps({"settings": settings}).encode(),
                    headers={"Content-Type":"application/json", "X-Aurora-Token":token}, method="POST")
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                deadline = time.monotonic() + 5
                while True:
                    status = json.load(urlopen(base + "/api/status", timeout=3))
                    if not status["summary"]["running"]:
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                self.assertEqual(status["summary"]["configuration"]["context_policy"], "balanced")
                self.assertIn("request_series", status["summary"]["context_management"])
                summary_path = Path(root) / status["summary"]["experiment_id"] / "summary.json"
                while not summary_path.is_file():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                saved = json.loads(summary_path.read_text())
                self.assertEqual(saved["configuration"]["context_policy"], "balanced")
            finally:
                server.dashboard_state.stop()
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()
