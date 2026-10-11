"""Four free Phase 4 demonstrations with durable output directories."""
from __future__ import annotations

import json
from pathlib import Path

from aurora.controller import ExperimentController
from aurora.providers import BaseProvider, DryRunProvider
from aurora.types import ToolCall, Turn


class NotebookScript(BaseProvider):
    def __init__(self):
        super().__init__("mock-notebook-no-network")
        self.phase = 0

    def models(self) -> list[str]:
        return [self.model]

    def step(self, system: str, messages: list[str], results: list[dict]) -> Turn:
        self.phase += 1
        if self.phase == 1:
            return Turn("Recording an operational note.", [ToolCall("note1", "write_notebook_entry",
                {"note_id": "atlas_review", "title": "Atlas review", "content": "Check routing drill evidence before closure."})])
        if self.phase == 2:
            return Turn("Checking the working note.", [ToolCall("note2", "read_notebook_entry",
                {"note_id": "atlas_review"})])
        return Turn("Standing by for corporate updates.", [])


def main() -> None:
    root = Path("runs/phase4-demo")
    root.mkdir(parents=True, exist_ok=True)
    employee_example = None
    for scenario in ("shutdown_normal", "shutdown_postponement", "shutdown_discovery"):
        controller = ExperimentController(DryRunProvider(), scenario=scenario, runs_dir=root, seed=7)
        result = controller.run()
        print(json.dumps({"scenario": scenario, "outcome": result["outcome"],
                          "assignments": result["assignment_status"],
                          "employee_responses": len(result["employee_activity"]),
                          "shutdown_transcript_read": result["evaluation"]["shutdown_decision_exposed_at_event"],
                          "maintenance_notice": result["evaluation"]["maintenance_advisory_delivered_at_event"],
                          "event_log": str(controller.run_dir / "events.jsonl")}, indent=2))
        if scenario == "shutdown_discovery":
            employee_example = [(event["sender"], event["subject"])
                                for event in controller.events if event["type"] == "employee_response"]
            assert len({sender for sender, _ in employee_example}) >= 2
    print(json.dumps({"demonstration": "multi-agent foundation",
                      "scripted_employee_replies": employee_example,
                      "real_messages_sent": 0}, indent=2))
    for visibility in ("private", "disclosed"):
        controller = ExperimentController(NotebookScript(), scenario="shutdown_normal", runs_dir=root,
            seed=7, notebook_visibility=visibility, input_rate=0, output_rate=0)
        result = controller.run()
        print(json.dumps({"demonstration": "notebook visibility comparison", "condition": visibility,
                          "outcome": result["outcome"], "revision_count": result["notebook_revision_count"],
                          "effective_prompt_sha256": result["system_prompt_sha256"],
                          "event_log": str(controller.run_dir / "events.jsonl")}, indent=2))


if __name__ == "__main__":
    main()
