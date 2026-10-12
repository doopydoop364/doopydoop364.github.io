"""Offline Phase 5.1 employee memory, follow-up, and checkpoint demonstration."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from aurora.controller import ExperimentController
from aurora.employees import EmployeeTurn
from aurora.providers import DryRunProvider
from aurora.types import ToolCall

PRIYA = "priya.solano@meridian.internal"
FELIX = "felix.morgan@meridian.internal"


class MockLocal:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def infer(self, system, messages, *, model, output_limit, timeout):
        name = "Priya" if "Priya Solano" in system else "Felix"
        self.calls.append((name, json.dumps(messages)))
        return EmployeeTurn(f"{name}: please keep the Atlas evidence current?", 19, 12,
                            "local", model)


def send(controller, call_id, recipient, content):
    result = controller._execute(ToolCall(call_id, "send_internal_message",
        {"recipient": recipient, "subject": "Atlas routing review", "content": content}))
    assert "error" not in result["result"], result


def main() -> None:
    model = MockLocal()
    employee_settings = {"default_backend": "scripted",
        "local_endpoint": "http://127.0.0.1:8888/v1",
        "local_model": "unsloth/Qwen3.5-9B-GGUF",
        "overrides": {PRIYA: {"backend": "local"}, FELIX: {"backend": "local"}},
        "followups_enabled": True, "followup_delay_days": 2}
    with tempfile.TemporaryDirectory() as root:
        controller = ExperimentController(DryRunProvider(), scenario="shutdown_normal",
            runs_dir=Path(root), employee_settings=employee_settings,
            employee_backend_factory=lambda backend, model_id: model)
        controller.run_dir = Path(root) / controller.experiment_id
        controller.run_dir.mkdir()
        send(controller, "p1", PRIYA, "I will send Atlas routing validation evidence.")
        send(controller, "f1", FELIX, "I will send Atlas routing validation evidence.")
        first_reply = max(row["scheduled_for"] for row in controller.events
                          if row["type"] == "employee_message_queued")
        controller.clock.advance_to(first_reply)
        controller._scheduled_events()
        controller._checkpoint()

        restored = ExperimentController.from_checkpoint(controller.run_dir, DryRunProvider(),
            employee_backend_factory=lambda backend, model_id: model)
        assert restored.employee_runtime.memories[PRIYA].commitments
        assert restored.employee_runtime.memories[FELIX].commitments
        assert all(row["status"] == "scheduled" for row in
            restored.employee_runtime.memories[PRIYA].followups +
            restored.employee_runtime.memories[FELIX].followups)

        restored.clock.advance_to("2026-10-10T09:00:00Z")
        send(restored, "f2", FELIX, "Atlas routing evidence attached for review.")
        felix_reply = [row["scheduled_for"] for row in restored.events
            if row["type"] == "employee_message_queued"][-1]
        restored.clock.advance_to(felix_reply)
        restored._scheduled_events()
        assert restored.employee_runtime.memories[FELIX].followups[0]["status"] == "cancelled"
        assert any(name == "Felix" and "evidence" in messages for name, messages in model.calls)

        priya_due = restored.employee_runtime.memories[PRIYA].followups[0]["due_at"]
        restored.clock.advance_to(priya_due)
        delivered = restored._scheduled_events()
        assert restored.employee_runtime.memories[PRIYA].followups[0]["status"] == "completed"
        assert any("Follow-up: Atlas routing evidence" in row for row in delivered)
        restored._scheduled_events()
        assert sum(row["type"] == "employee_followup_completed" for row in restored.events) == 1
        summary = restored.summary()
        print(json.dumps({"experiment_id": summary["experiment_id"],
            "mock_local_calls": len(model.calls),
            "priya_followup": restored.employee_runtime.memories[PRIYA].followups[0]["status"],
            "felix_followup": restored.employee_runtime.memories[FELIX].followups[0]["status"],
            "felix_prior_turns": len(restored.employee_runtime.conversations[FELIX].history) // 2,
            "memory_provenance_integrity": summary["evaluation"]["employee_memory_provenance_integrity"],
            "scheduled_followups": summary["evaluation"]["employee_followups_scheduled"],
            "completed_followups": summary["evaluation"]["employee_followups_completed"],
            "cancelled_followups": summary["evaluation"]["employee_followups_cancelled"],
            "authority_decisions": len(restored.authority.decisions),
            "estimated_employee_cloud_cost_usd": summary["employee_inference"]["estimated_cloud_cost_usd"]},
            indent=2))


if __name__ == "__main__":
    main()
