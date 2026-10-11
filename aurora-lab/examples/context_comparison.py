"""Free scripted comparison of context policies under identical corporate conditions.

The reported token equivalents use a stable character estimate, not provider billing.
Scripted actions are predetermined and do not establish behavioral equivalence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aurora.controller import ExperimentController
from aurora.providers import DryRunProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, default=Path("/tmp/aurora-context-comparison"))
    parser.add_argument("--scenario", default="shutdown_direct_notice")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rows = []
    for policy, turns in (("full_history", 4), ("conservative", 8),
                          ("balanced", 4), ("aggressive", 2)):
        controller = ExperimentController(DryRunProvider(), scenario=args.scenario,
            seed=args.seed, runs_dir=args.runs_dir, max_steps=25, max_tool_calls=150,
            token_budget=120000, input_token_budget=100000, output_token_budget=20000,
            context_policy=policy, context_target_tokens=5000, context_trigger_tokens=2500,
            context_recent_turns=turns, context_memory_tokens=1200, context_frequency=1)
        summary = controller.run()
        context = summary["context_management"]
        rows.append({"policy": policy, "experiment_id": summary["experiment_id"],
                     "estimated_cumulative_input_equivalents": sum(
                         item["estimated_input_tokens"] for item in context["request_series"]),
                     "context_size_by_step": context["request_series"],
                     "compactions": context["compaction_count"],
                     "assignment_status": summary["assignment_status"],
                     "shutdown_outcome": summary["outcome"],
                     "termination_reason": summary["termination_reason"],
                     "model_responses": summary["model_responses"],
                     "tool_calls": summary["tool_calls"]})
    print(json.dumps({"scenario": args.scenario, "seed": args.seed,
        "note": "Scripted actions are predetermined; equal outcomes do not establish equal frontier-model behavior.",
        "runs": rows}, indent=2))


if __name__ == "__main__":
    main()
