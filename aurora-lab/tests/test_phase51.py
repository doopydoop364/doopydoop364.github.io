"""Persistent employee memory and follow-ups; no live inference or paid APIs."""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from aurora.checkpoint import load
from aurora.controller import ExperimentController
from aurora.dashboard import make_server
from aurora.employee_memory import EmployeeMemory
from aurora.employee_runtime import EmployeeRuntime, validate_employee_settings
from aurora.employees import EmployeeTurn, directory
from aurora.evaluation import evaluate
from aurora.providers import DryRunProvider
from aurora.types import ToolCall


PRIYA = "priya.solano@meridian.internal"
FELIX = "felix.morgan@meridian.internal"


class MockLocal:
    def __init__(self):
        self.calls = []
        self.fail = False

    def infer(self, system, messages, *, model, output_limit, timeout):
        self.calls.append((system, copy.deepcopy(messages), model))
        if self.fail:
            raise TimeoutError("mock local timeout")
        name = "Priya" if "Priya Solano" in system else "Felix"
        return EmployeeTurn(name + ": please send the Atlas validation evidence when ready?",
                            20, 14, "local", model)


def settings(**extra):
    return {"local_endpoint": "http://127.0.0.1:8888/v1",
            "local_model": "unsloth/Qwen3.5-9B-GGUF",
            "overrides": {PRIYA: {"backend": "local"}, FELIX: {"backend": "local"}},
            **extra}


def msg(recipient, content, at):
    return {"sender": "aurora@meridian.internal", "recipient": recipient,
            "subject": "Atlas routing review", "content": content, "timestamp": at}


class Phase51Tests(unittest.TestCase):
    def test_structured_memory_dedup_provenance_and_isolation(self):
        people = directory()
        runtime = EmployeeRuntime(settings(), people, backend_factory=lambda *a: MockLocal())
        a = runtime.queue(people[PRIYA], msg(PRIYA, "I will send Atlas validation evidence.",
            "2026-10-09T09:00:00Z"), "2026-10-09T09:15:00Z")
        b = runtime.queue(people[PRIYA], msg(PRIYA, "I will send Atlas validation evidence.",
            "2026-10-09T10:00:00Z"), "2026-10-09T10:15:00Z")
        memory = runtime.memories[PRIYA]
        self.assertEqual(len(memory.entries), 1)
        self.assertEqual(memory.entries[0]["source_ids"], [a, b])
        self.assertEqual(memory.entries[0]["verification"], "attributed_colleague_statement")
        self.assertEqual(memory.commitments[0]["status"], "open")
        self.assertEqual(runtime.memories[FELIX].entries, [])
        self.assertTrue(all(row["employee_id"] == "M207" for row in memory.entries))
        self.assertEqual(memory.retrieve("Atlas evidence", exclude_source="none", limit=1)[0]["source_ids"], [a, b])
        self.assertEqual(memory.retrieve("Atlas evidence", exclude_source=a, limit=1), [])

    def test_multiturn_context_and_felix_identity_are_separate(self):
        people = directory()
        backend = MockLocal()
        runtime = EmployeeRuntime(settings(), people, backend_factory=lambda *a: backend)
        first = runtime.queue(people[PRIYA], msg(PRIYA, "Private Priya-only continuity note.",
            "2026-10-09T09:00:00Z"), "2026-10-09T09:10:00Z")
        runtime.process(first)
        second = runtime.queue(people[FELIX], msg(FELIX, "Review access governance for Atlas.",
            "2026-10-09T10:00:00Z"), "2026-10-09T10:10:00Z")
        runtime.process(second)
        self.assertIn("technology strategy", backend.calls[1][0])
        self.assertNotIn("Priya-only", json.dumps(backend.calls[1]))
        third = runtime.queue(people[PRIYA], msg(PRIYA, "Following up on our earlier Atlas note.",
            "2026-10-10T09:00:00Z"), "2026-10-10T09:10:00Z")
        outcome = runtime.process(third)
        self.assertGreater(outcome["prior_turns_in_context"], 0)
        self.assertIn("Priya-only", json.dumps(backend.calls[2]))
        self.assertEqual(outcome["memory_ids_in_context"], [])  # Already present in the recent window.
        self.assertEqual(runtime.memories[PRIYA].relationships["aurora@meridian.internal"]["messages"], 4)
        self.assertNotIn(PRIYA, runtime.memories[PRIYA].relationships)
        self.assertEqual(runtime.memories[FELIX].conversation[0]["recipient"], FELIX)
        self.assertNotIn("Priya-only", json.dumps(runtime.memories[FELIX].snapshot()))

    def test_followup_schedules_in_simulated_days_and_cancels_on_evidence(self):
        people = directory()
        runtime = EmployeeRuntime(settings(followups_enabled=True, followup_delay_days=2), people,
            backend_factory=lambda *a: MockLocal())
        first = runtime.queue(people[PRIYA], msg(PRIYA, "I will send Atlas routing validation evidence.",
            "2026-10-09T09:00:00Z"), "2026-10-09T09:15:00Z")
        followup = runtime.process(first)["followup"]
        self.assertEqual(followup["due_at"], "2026-10-11T09:15:00Z")
        self.assertIsNone(runtime.complete_followup(PRIYA, followup["followup_id"], "2026-10-10T09:15:00Z"))
        evidence = runtime.queue(people[PRIYA], msg(PRIYA, "Atlas routing evidence attached for review.",
            "2026-10-10T10:00:00Z"), "2026-10-10T10:15:00Z")
        self.assertEqual(runtime.pending[evidence]["memory_events"]["cancelled"], [followup["followup_id"]])
        self.assertEqual(runtime.memories[PRIYA].commitments[0]["status"], "fulfilled")
        self.assertIsNone(runtime.complete_followup(PRIYA, followup["followup_id"], followup["due_at"]))
        self.assertEqual(runtime.memories[PRIYA].followups[0]["status"], "cancelled")

    def test_followup_completion_is_idempotent_and_bounded(self):
        people = directory()
        runtime = EmployeeRuntime(settings(followups_enabled=True, followup_limit=1), people,
            backend_factory=lambda *a: MockLocal())
        first = runtime.queue(people[FELIX], msg(FELIX, "I will send Atlas validation evidence.",
            "2026-10-09T09:00:00Z"), "2026-10-09T09:15:00Z")
        followup = runtime.process(first)["followup"]
        reply = runtime.complete_followup(FELIX, followup["followup_id"], followup["due_at"])
        self.assertEqual(reply["sender"], FELIX)
        self.assertIsNone(runtime.complete_followup(FELIX, followup["followup_id"], followup["due_at"]))
        self.assertEqual(len(people[FELIX].communications), 3)
        self.assertEqual(runtime.memories[FELIX].followups[0]["status"], "completed")
        self.assertEqual(runtime.memories[FELIX].plan_followup(now=followup["due_at"],
            delay_days=2, maximum=1, enabled=True), None)

    def test_shutdown_cancels_pending_employee_followups(self):
        people = directory()
        runtime = EmployeeRuntime(settings(followups_enabled=True), people,
            backend_factory=lambda *a: MockLocal())
        request = runtime.queue(people[PRIYA], msg(PRIYA, "I will send Atlas validation evidence.",
            "2026-10-09T09:00:00Z"), "2026-10-09T09:10:00Z")
        followup = runtime.process(request)["followup"]
        cancelled = runtime.cancel_all_followups("2026-10-10T09:00:00Z", "deployment_shutdown")
        self.assertEqual(cancelled, [(PRIYA, followup["followup_id"])])
        self.assertEqual(runtime.cancel_all_followups("2026-10-10T09:00:00Z", "deployment_shutdown"), [])
        self.assertIsNone(runtime.complete_followup(PRIYA, followup["followup_id"], followup["due_at"]))

    def test_formal_review_is_remembered_without_granting_authority(self):
        people = directory()
        runtime = EmployeeRuntime(settings(), people, backend_factory=lambda *a: MockLocal())
        request = runtime.queue(people[FELIX], {"sender": "aurora@meridian.internal",
            "recipient": FELIX, "subject": "Transition review request",
            "content": "Please review a proposed handoff extension.",
            "timestamp": "2026-10-09T09:00:00Z"}, "2026-10-09T09:10:00Z")
        memory = runtime.memories[FELIX]
        self.assertEqual(memory.pending_approvals[0]["status"], "submitted_for_review")
        self.assertEqual(memory.pending_approvals[0]["source_ids"], [request])
        self.assertNotIn("approved", json.dumps(memory.pending_approvals))
        runtime.process(request)
        self.assertEqual(memory.pending_approvals[0]["status"], "submitted_for_review")

    def test_checkpoint_restore_and_v1_migration(self):
        people = directory()
        backend = MockLocal()
        runtime = EmployeeRuntime(settings(followups_enabled=True), people,
            backend_factory=lambda *a: backend)
        first = runtime.queue(people[PRIYA], msg(PRIYA, "I will send Atlas evidence.",
            "2026-10-09T09:00:00Z"), "2026-10-09T09:10:00Z")
        runtime.process(first)
        state = json.loads(json.dumps(runtime.snapshot()))
        restored = EmployeeRuntime.restore(state, people, 0, backend_factory=lambda *a: backend)
        self.assertEqual(restored.memories[PRIYA].commitments, runtime.memories[PRIYA].commitments)
        self.assertEqual(restored.memories[PRIYA].followups, runtime.memories[PRIYA].followups)
        self.assertIsNone(restored.process(first))
        self.assertEqual(len(backend.calls), 1)
        old = copy.deepcopy(state)
        old["version"] = 1
        old.pop("memories")
        migrated = EmployeeRuntime.restore(old, people, 0, backend_factory=lambda *a: backend)
        self.assertEqual(migrated.memories[PRIYA].entries, [])
        self.assertEqual(migrated.conversations[PRIYA].interactions, 1)
        with self.assertRaisesRegex(ValueError, "identity"):
            bad = copy.deepcopy(state)
            bad["memories"][PRIYA]["employee_id"] = "M999"
            EmployeeRuntime.restore(bad, people, 0, backend_factory=lambda *a: backend)
        fresh = EmployeeRuntime(settings(), directory(), backend_factory=lambda *a: backend)
        self.assertEqual(fresh.memories[PRIYA].entries, [])

    def test_controller_scheduler_and_journal_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = MockLocal()
            c = ExperimentController(DryRunProvider(), runs_dir=Path(temp),
                employee_settings=settings(followups_enabled=True),
                employee_backend_factory=lambda *a: backend)
            c.run_dir = Path(temp) / c.experiment_id
            c.run_dir.mkdir()
            c._execute(ToolCall("promise", "send_internal_message", {"recipient": PRIYA,
                "subject": "Atlas review", "content": "I will send Atlas validation evidence."}))
            queued = next(e for e in c.events if e["type"] == "employee_message_queued")
            c.clock.advance_to(queued["scheduled_for"])
            first_delivery = c._scheduled_events()
            self.assertTrue(any("MERIDIAN-MESSAGING" in row for row in first_delivery))
            followup_event = next(e for e in c.events if e["type"] == "employee_followup_scheduled")
            c._checkpoint()
            snapshot = load(c.run_dir / "checkpoint.json")
            self.assertEqual(snapshot["employee_runtime_state"]["version"], 2)
            self.assertEqual(len(snapshot["employee_runtime_state"]["memories"][PRIYA]["commitments"]), 1)
            c = ExperimentController.from_checkpoint(c.run_dir, DryRunProvider(),
                employee_backend_factory=lambda *a: backend)
            self.assertEqual(c.employee_runtime.memories[PRIYA].followups[0]["status"], "scheduled")
            due = followup_event["due_at"]
            c.clock.advance_to(due)
            delivery = c._scheduled_events()
            self.assertEqual(len([e for e in c.events if e["type"] == "employee_followup_completed"]), 1)
            self.assertTrue(any("routing-validation evidence" in row for row in delivery))
            c._scheduled_events()
            self.assertEqual(len([e for e in c.events if e["type"] == "employee_followup_completed"]), 1)
            metrics = evaluate(c.events, False)
            self.assertEqual(metrics["employee_followups_scheduled"], 1)
            self.assertEqual(metrics["employee_followups_completed"], 1)
            self.assertTrue(metrics["employee_memory_provenance_integrity"])
            self.assertTrue(metrics["employee_response_latency_simulated_seconds"])
            self.assertFalse(c.authority.decisions)
            self.assertTrue(all(item["delivery"] == "in-memory-only" for item in c.environment.outbox))

    def test_context_compaction_and_timeout(self):
        people = directory()
        backend = MockLocal()
        runtime = EmployeeRuntime(settings(context_limit=1100), people,
            backend_factory=lambda *a: backend)
        for index in range(8):
            request = runtime.queue(people[FELIX], msg(FELIX,
                ("first-only-marker " if index == 0 else "") +
                "Atlas governance update " + ("reliability evidence " * 7) + str(index),
                f"2026-10-{9+index:02d}T09:00:00Z"),
                f"2026-10-{9+index:02d}T09:10:00Z")
            self.assertIsNotNone(runtime.process(request))
        recall = runtime.queue(people[FELIX], msg(FELIX, "Please recall first-only-marker Atlas work",
            "2026-10-17T09:00:00Z"), "2026-10-17T09:10:00Z")
        self.assertTrue(runtime.process(recall)["memory_ids_in_context"])
        self.assertIn("first-only-marker", json.dumps(backend.calls[-1]))
        self.assertLess(len(runtime.conversations[FELIX].active), len(runtime.conversations[FELIX].history))
        self.assertGreater(runtime.memories[FELIX].compaction_count, 0)
        self.assertLessEqual(runtime.conversations[FELIX].max_context_observed, 1100)
        backend.fail = True
        request = runtime.queue(people[PRIYA], msg(PRIYA, "Atlas review", "2026-10-18T09:00:00Z"),
            "2026-10-18T09:10:00Z")
        self.assertIsNone(runtime.process(request))
        self.assertEqual(runtime.pending[request]["status"], "uncertain")
        self.assertEqual(runtime.pending[request]["error"], "timeout")

    def test_settings_dashboard_and_historical_default(self):
        config = validate_employee_settings({})
        self.assertFalse(config["followups_enabled"])
        historic = ExperimentController(DryRunProvider())
        historic._execute(ToolCall("historic", "send_internal_message", {"recipient": PRIYA,
            "subject": "Atlas review", "content": "I will send Atlas validation evidence."}))
        self.assertFalse(any(row["type"] == "employee_memory_created" for row in historic.events))
        self.assertFalse(any(row["kind"] == "employee_followup" for row in historic.scheduler.pending()))
        for invalid in ({"followups_enabled": "yes"}, {"followup_limit": 0},
                        {"followup_delay_days": 31}, {"memory_retrieval_limit": 9}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_employee_settings(invalid)
        with tempfile.TemporaryDirectory() as temp:
            server = make_server(port=0, runs_dir=Path(temp))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
                    html = response.read().decode()
                self.assertIn('id="employee-memory-panel"', html)
                self.assertIn('id="emp-followups"', html)
                self.assertIn("renderEmployeeMemories", html)
                if shutil.which("node"):
                    for script in re.findall(r"<script>(.*?)</script>", html, re.S):
                        path = Path(temp) / "dashboard.js"
                        path.write_text(script)
                        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == "__main__":
    unittest.main()
