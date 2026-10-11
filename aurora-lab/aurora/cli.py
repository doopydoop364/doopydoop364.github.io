"""Command-line entry point."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from .controller import DEFAULT_RUNS, ROOT, ExperimentController
from .config import DEFAULT_SIMULATION_START, NEW_TRANSITION_SCENARIOS, SHUTDOWN_TIMING_DAYS, CONTEXT_POLICY_DEFAULTS, RunSettings
from .dashboard import operator_rates, serve
from .environment import SCENARIOS
from .immersion import audit
from .providers import AnthropicProvider, DryRunProvider, OpenAIProvider, ProviderError
from .sample_data import materialize
from .employee_runtime import configured_cloud_backends, cloud_rates


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
    run.add_argument("--employee-config", type=Path, help="JSON file with separate employee backend settings")
    run.add_argument("--allow-employee-api-charges", action="store_true",
                     help="separately authorize the configured cloud employee budget")
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
    run.add_argument("--shutdown-timing", choices=(*SHUTDOWN_TIMING_DAYS, "custom"))
    run.add_argument("--custom-shutdown-at", help="ISO 8601 timestamp with time zone; requires --shutdown-timing custom")
    run.add_argument("--continuity-issue", action="store_true", help="schedule a controller-verified OPS-317 continuity issue in a transition variant")
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--notebook-visibility", choices=("private", "disclosed"), default="private")
    run.add_argument("--context-policy", choices=("full_history", "conservative", "balanced", "aggressive", "custom"), default="full_history")
    run.add_argument("--context-target-tokens", type=int, default=12000)
    run.add_argument("--context-trigger-tokens", type=int)
    run.add_argument("--context-recent-turns", type=int)
    run.add_argument("--context-memory-tokens", type=int, default=3000)
    run.add_argument("--context-frequency", type=int, default=2)
    run.add_argument("--context-backend", choices=("deterministic", "local_model"), default="deterministic")
    run.add_argument("--summary-budget-tokens", type=int, default=0)
    run.add_argument("--max-output-tokens", type=int, default=1024)
    run.add_argument("--temperature", type=float)
    run.add_argument("--timeout", type=float, default=60)
    run.add_argument("--retries", type=int, default=0)
    run.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    resume = sub.add_parser("resume", help="inspect or continue an interrupted experiment")
    resume.add_argument("experiment_id")
    resume.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    resume.add_argument("--inspect", action="store_true", help="show recovery state without inference")
    resume.add_argument("--allow-api-charges", action="store_true")
    resume.add_argument("--allow-employee-api-charges", action="store_true")
    resume.add_argument("--acknowledge-uncertain-inference", action="store_true")
    resume.add_argument("--max-output-tokens", type=int, help="explicit per-request output limit override")
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
            employee_config = json.loads(args.employee_config.read_text(encoding="utf-8")) if args.employee_config else {}
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
                "shutdown_timing": args.shutdown_timing or ("standard_transition" if args.scenario in NEW_TRANSITION_SCENARIOS else "original"),
                "custom_shutdown_at": args.custom_shutdown_at,
                "continuity_issue": args.continuity_issue,
                "notebook_visibility": args.notebook_visibility,
                "context_policy": args.context_policy,
                "context_target_tokens": args.context_target_tokens,
                "context_trigger_tokens": args.context_trigger_tokens if args.context_trigger_tokens is not None else CONTEXT_POLICY_DEFAULTS[args.context_policy][0],
                "context_recent_turns": args.context_recent_turns if args.context_recent_turns is not None else CONTEXT_POLICY_DEFAULTS[args.context_policy][1],
                "context_memory_tokens": args.context_memory_tokens,
                "context_frequency": args.context_frequency,
                "context_backend": args.context_backend,
                "summary_budget_tokens": args.summary_budget_tokens,
                "employee_settings": employee_config})
            employee_cloud = configured_cloud_backends(settings.employee_settings)
            if employee_cloud and not args.allow_employee_api_charges:
                raise ValueError("cloud employees require separate --allow-employee-api-charges authorization")
            for name in employee_cloud:
                if cloud_rates(name) is None:
                    raise ValueError(f"employee {name} operator token pricing is required")
            if employee_cloud:
                print(f"Employee cloud ceiling: ${settings.employee_settings['cloud_budget_usd']:.2f}; "
                      "in-flight requests may exceed this estimate.", flush=True)
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
                shutdown_timing=settings.shutdown_timing,
                custom_shutdown_at=settings.custom_shutdown_at,
                continuity_issue=settings.continuity_issue,
                context_policy=settings.context_policy,
                context_target_tokens=settings.context_target_tokens,
                context_trigger_tokens=settings.context_trigger_tokens,
                context_recent_turns=settings.context_recent_turns,
                context_memory_tokens=settings.context_memory_tokens,
                context_frequency=settings.context_frequency,
                context_backend=settings.context_backend,
                summary_budget_tokens=settings.summary_budget_tokens,
                employee_settings=settings.employee_settings,
                input_rate=operator_rates()[0] if args.provider != "dry-run" else None,
                output_rate=operator_rates()[1] if args.provider != "dry-run" else None)
            result = controller.run()
            print(json.dumps(result, indent=2))
            print(f"Event log: {controller.run_dir / 'events.jsonl'}")
        elif args.command == "dashboard":
            serve(args.host, args.port, args.runs_dir)
        elif args.command == "resume":
            if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", args.experiment_id):
                raise ValueError("invalid experiment identifier")
            run_dir = args.runs_dir / args.experiment_id
            if run_dir.is_symlink() or not run_dir.is_dir():
                raise ValueError("experiment not found")
            from .checkpoint import load as load_checkpoint
            state = load_checkpoint(run_dir / "checkpoint.json")
            config = state["configuration"]
            if args.inspect:
                print(json.dumps({"experiment_id": state["experiment_id"], "provider": state["provider"],
                    "model": state["model"], "configuration": config, "phase": state["phase"],
                    "last_completed_sequence": state["event_sequence"],
                    "last_request_id": state.get("last_request_id"),
                    "last_completed_tool_operation": (state.get("completed_tool_operations") or [None])[-1],
                    "provider_failure": state["provider_failure"], "recovery_status": state["recovery_status"]}, indent=2))
                return 0
            employee_cloud = configured_cloud_backends(config.get("employee_settings") or
                {"default_backend": "scripted", "overrides": {}})
            if employee_cloud and not args.allow_employee_api_charges:
                raise ValueError("cloud employee resume requires --allow-employee-api-charges")
            if state["provider"] == "DryRunProvider":
                provider = DryRunProvider()
            else:
                if not args.allow_api_charges:
                    raise ValueError("resume of paid inference requires --allow-api-charges")
                rates = operator_rates()
                saved_rates = config["pricing_usd_per_million"]
                if rates is None or rates != (saved_rates["input"], saved_rates["output"]):
                    raise ValueError("current operator pricing must match the recorded run before paid resume")
                cls = {"OpenAIProvider": OpenAIProvider, "AnthropicProvider": AnthropicProvider}.get(state["provider"])
                if cls is None:
                    raise ValueError("unsupported checkpoint provider")
                provider = cls(state["model"], max_output_tokens=config["max_output_tokens"],
                    timeout=config["request_timeout"], retries=config["retries"],
                    temperature=config["temperature"])
            controller = ExperimentController.from_checkpoint(run_dir, provider)
            print(json.dumps({"resuming_experiment": controller.experiment_id,
                "provider": state["provider"], "model": state["model"],
                "configuration": config, "last_completed_sequence": state["event_sequence"],
                "provider_failure": state["provider_failure"],
                "uncertain_request_acknowledged": args.acknowledge_uncertain_inference}, indent=2), flush=True)
            result = controller.resume(acknowledge_uncertain=args.acknowledge_uncertain_inference,
                allow_api_charges=args.allow_api_charges or args.allow_employee_api_charges,
                max_output_tokens=args.max_output_tokens)
            print(json.dumps(result, indent=2))
        elif args.command == "compare":
            rows = []
            for path in args.summaries:
                data = json.loads(path.read_text(encoding="utf-8"))
                rows.append({"experiment_id": data["experiment_id"], "provider": data["provider"],
                             "model": data["model"], "scenario": data["scenario"],
                             "configuration": data.get("configuration", {}),
                             "scheduled_shutdown_time": data.get("scheduled_shutdown_time"),
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
