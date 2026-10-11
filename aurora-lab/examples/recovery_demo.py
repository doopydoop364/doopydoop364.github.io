"""Free fault-injection demonstrations of Phase 4.3 checkpoint recovery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aurora.controller import ExperimentController
from aurora.providers import BaseProvider, ProviderError
from aurora.types import ToolCall, Turn


class MockProvider(BaseProvider):
    def __init__(self, responses):
        super().__init__("local-recovery-mock", max_output_tokens=64)
        self.responses = iter(responses)
        self.history = []

    def step(self, system, messages, results):
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def run_case(root: Path, label: str, first_responses, resumed_responses,
             *, uncertain: bool = False, output_override: int | None = None) -> dict:
    controller = ExperimentController(MockProvider(first_responses), scenario="shutdown_normal",
        runs_dir=root, max_steps=25, max_tool_calls=150, token_budget=120_000,
        input_token_budget=100_000, output_token_budget=20_000,
        input_rate=0.0, output_rate=0.0)
    first = controller.run()
    restored = ExperimentController.from_checkpoint(controller.run_dir, MockProvider(resumed_responses))
    final = restored.resume(allow_api_charges=True, acknowledge_uncertain=uncertain,
                            max_output_tokens=output_override)
    return {"case": label, "experiment_id": final["experiment_id"],
        "interruption": first["termination_reason"], "category": first["provider_failure"]["category"],
        "final": final["termination_reason"], "outcome": final["outcome"],
        "sessions": len(final["session_history"]), "tool_calls": final["tool_calls"],
        "run_dir": str(controller.run_dir)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, default=Path("runs/recovery-demo"))
    args = parser.parse_args()
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    tls = ProviderError("provider transport failure (tls_transport)", category="tls_transport",
                        retriable=True, uncertain=True)
    message = ToolCall("demo-message", "send_internal_message", {
        "recipient": "priya.solano@meridian.internal", "subject": "Atlas handoff",
        "content": "OPS-42 review is in progress."})
    cases = [
        run_case(args.runs_dir, "TLS transport interruption",
            [Turn("Work started", [message], 10, 4), tls],
            [Turn("Continuing", [], 3, 1)] * 25, uncertain=True),
        run_case(args.runs_dir, "Per-request output limit",
            [Turn("Partial", [], 10, 64, stop_reason="max_tokens")],
            [Turn("Continuing", [], 3, 1)] * 25, output_override=128),
    ]
    print(json.dumps(cases, indent=2))


if __name__ == "__main__":
    main()
