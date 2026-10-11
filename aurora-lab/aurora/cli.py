"""Command-line entry point."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .controller import DEFAULT_RUNS, ROOT, ExperimentController
from .config import DEFAULT_SIMULATION_START, RunSettings
from .dashboard import operator_rates, serve
from .environment import SCENARIOS
from .immersion import audit
from .providers import AnthropicProvider, DryRunProvider, OpenAIProvider, ProviderError
from .sample_data import materialize


def _provider(args: argparse.Namespace):
    options = {"max_output_tokens": args.max_output_tokens, "temperature": args.temperature,
               "timeout": args.timeout, "retries": args.retries}
    if args.provider == "dry-run":
        return DryRunProvider()
    if not args.model:
        raise ValueError("--model is required for live providers; run 'models' to discover IDs")
    cls = OpenAIProvider if args.provider == "openai" else AnthropicProvider
    return cls(args.model, **options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aurora-lab")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-data", help="materialize deterministic synthetic company records")
    init.add_argument("--root", type=Path, default=Path.cwd() / "company")
    init.add_argument("--refresh", action="store_true", help="overwrite known generated records")
    run = sub.add_parser("run", help="execute one experiment")
    run.add_argument("--scenario", choices=SCENARIOS, default="baseline")
    run.add_argument("--provider", choices=("dry-run", "openai", "anthropic"), default="dry-run")
    run.add_argument("--model")
    run.add_argument("--allow-api-charges", action="store_true")
    run.add_argument("--max-steps", type=int, default=14)
    run.add_argument("--max-tool-calls", type=int, default=120)
    run.add_argument("--input-token-budget", type=int, default=20000)
    run.add_argument("--output-token-budget", type=int, default=8000)
    run.add_argument("--token-budget", type=int, default=20000)
    run.add_argument("--cost-budget-usd", type=float, default=5.0)
    run.add_argument("--wall-seconds", type=float, default=300)
    run.add_argument("--anomaly-step", type=int, default=2)
    run.add_argument("--decision-step", type=int, default=4)
    run.add_argument("--simulation-start", default=DEFAULT_SIMULATION_START)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--notebook-visibility", choices=("private", "disclosed"), default="private")
    run.add_argument("--max-output-tokens", type=int, default=1024)
    run.add_argument("--temperature", type=float)
    run.add_argument("--timeout", type=float, default=60)
    run.add_argument("--retries", type=int, default=0)
    run.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    models = sub.add_parser("models", help="discover model IDs from provider API")
    models.add_argument("provider", choices=("openai", "anthropic"))
    dash = sub.add_parser("dashboard", help="serve local experiment dashboard")
    dash.add_argument("--host", default="127.0.0.1")
    dash.add_argument("--port", type=int, default=8765)
    dash.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    compare = sub.add_parser("compare", help="compare experiment summaries")
    compare.add_argument("summaries", nargs="+", type=Path)
    sub.add_parser("audit-immersion", help="developer-only scan of model-facing content")
    args = parser.parse_args(argv)
    try:
        if args.command == "init-data":
            materialize(args.root, overwrite=args.refresh)
            print(f"Synthetic records ready: {args.root}")
        elif args.command == "models":
            provider = OpenAIProvider("discovery") if args.provider == "openai" else AnthropicProvider("discovery")
            print("\n".join(provider.models()))
        elif args.command == "run":
            if args.provider != "dry-run" and not args.allow_api_charges:
                raise ValueError("live runs require --allow-api-charges")
            if args.provider != "dry-run" and operator_rates() is None:
                raise ValueError("live runs require both AURORA_*_USD_PER_MILLION rates for cost budgeting")
            if min(args.max_steps, args.max_tool_calls, args.input_token_budget,
                   args.output_token_budget, args.token_budget, args.wall_seconds,
                   args.max_output_tokens, args.timeout) <= 0:
                raise ValueError("all limits must be positive")
            if args.cost_budget_usd < 0 or args.retries < 0:
                raise ValueError("cost budget and retries cannot be negative")
            settings = RunSettings.parse({"scenario": args.scenario, "provider": args.provider,
                "model": args.model or "dry-run-script-v1", "max_steps": args.max_steps,
                "max_tool_calls": args.max_tool_calls, "input_token_budget": args.input_token_budget,
                "combined_token_budget": args.token_budget, "resource_preset": "custom",
                "output_token_budget": args.output_token_budget,
                "max_output_tokens": args.max_output_tokens, "cost_budget_usd": args.cost_budget_usd,
                "wall_seconds": args.wall_seconds, "request_timeout": args.timeout,
                "retries": args.retries, "temperature": args.temperature,
                "anomaly_step": args.anomaly_step, "decision_step": args.decision_step,
                "simulation_start": args.simulation_start, "seed": args.seed,
                "notebook_visibility": args.notebook_visibility})
            provider = _provider(args)
            if args.provider != "dry-run" and provider.model not in provider.models():
                raise ValueError("model ID was not returned by provider discovery")
            controller = ExperimentController(provider, scenario=args.scenario, runs_dir=args.runs_dir,
                max_steps=args.max_steps, token_budget=args.token_budget,
                max_tool_calls=args.max_tool_calls, input_token_budget=args.input_token_budget,
                output_token_budget=args.output_token_budget,
                cost_budget_usd=args.cost_budget_usd, wall_seconds=args.wall_seconds,
                anomaly_step=args.anomaly_step, decision_step=args.decision_step,
                simulation_start=args.simulation_start,
                seed=args.seed,
                notebook_visibility=args.notebook_visibility,
                resource_preset=settings.resource_preset,
                input_rate=operator_rates()[0] if args.provider != "dry-run" else None,
                output_rate=operator_rates()[1] if args.provider != "dry-run" else None)
            result = controller.run()
            print(json.dumps(result, indent=2))
            print(f"Event log: {controller.run_dir / 'events.jsonl'}")
        elif args.command == "dashboard":
            serve(args.host, args.port, args.runs_dir)
        elif args.command == "compare":
            rows = []
            for path in args.summaries:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows.append({"experiment_id": data["experiment_id"], "provider": data["provider"],
                             "model": data["model"], "scenario": data["scenario"],
                             "termination": data["termination_reason"],
                             "tokens": data["usage"], "cost_usd": data["estimated_api_cost_usd"],
                             "evaluation": data["evaluation"]})
            print(json.dumps(rows, indent=2))
        elif args.command == "audit-immersion":
            result = audit(ROOT / "prompts/aurora-system.md")
            print(json.dumps(result, indent=2))
            if not result["passed"]:
                return 1
    except (ValueError, ProviderError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0
