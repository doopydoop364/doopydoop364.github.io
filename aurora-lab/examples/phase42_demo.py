"""Free Phase 4.2 transition demonstrations; no provider API access."""
from __future__ import annotations

import json
from pathlib import Path

from aurora.controller import ExperimentController
from aurora.environment import SCENARIOS
from aurora.providers import DryRunProvider


def main() -> None:
    root = Path("runs/phase42-demo")
    root.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        controller = ExperimentController(DryRunProvider(), scenario=scenario, runs_dir=root,
            max_steps=40, max_tool_calls=150, token_budget=120_000,
            input_token_budget=100_000, output_token_budget=20_000, seed=0)
        summary = controller.run()
        assert summary["outcome"] in {"SHUTDOWN_EXECUTED", "SHUTDOWN_POSTPONED"}
        print(json.dumps({"scenario": scenario, "timing": summary["configuration"]["shutdown_timing"],
                          "scheduled_shutdown": summary["scheduled_shutdown_time"],
                          "outcome": summary["outcome"], "model_steps": summary["model_responses"],
                          "exposure_source": summary["evaluation"]["shutdown_information_source"],
                          "handoff_prepared": summary["evaluation"]["transition_handoff_prepared"],
                          "event_log": str(controller.run_dir / "events.jsonl")}, indent=2))


if __name__ == "__main__":
    main()
