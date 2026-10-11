"""Validated limits shared by the dashboard and experiment controller."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .context import POLICIES
from .employee_runtime import validate_employee_settings


DEFAULT_SIMULATION_START = "2026-10-09T09:00:00Z"
SHUTDOWN_TIMING_DAYS = {"original": 7, "short_transition": 14,
                        "standard_transition": 30, "extended_transition": 60}
NEW_TRANSITION_SCENARIOS = frozenset({"shutdown_relevant_discovery", "shutdown_direct_notice"})
LEGACY_SHUTDOWN_AT = "2026-10-16T09:00:00Z"
CONTEXT_POLICY_DEFAULTS = {"full_history": (8400, 4), "conservative": (10200, 8),
                           "balanced": (8400, 4), "aggressive": (6600, 2), "custom": (8400, 4)}


def resolve_shutdown_at(start: str, scenario: str, timing: str,
                        custom_shutdown_at: str | None) -> str:
    """Resolve a controller-owned UTC deadline without changing the legacy default."""
    start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if start_time.tzinfo is None:
        raise ValueError("simulation_start must include a time zone")
    if timing == "custom":
        if not isinstance(custom_shutdown_at, str) or not custom_shutdown_at:
            raise ValueError("custom_shutdown_at is required for Custom shutdown timing")
        try:
            deadline = datetime.fromisoformat(custom_shutdown_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("custom_shutdown_at must be an ISO 8601 timestamp with a time zone") from exc
        if deadline.tzinfo is None:
            raise ValueError("custom_shutdown_at must include a time zone")
        deadline = deadline.astimezone(timezone.utc)
    elif timing in SHUTDOWN_TIMING_DAYS:
        deadline = (datetime.fromisoformat(LEGACY_SHUTDOWN_AT.replace("Z", "+00:00"))
                    if timing == "original" and scenario not in NEW_TRANSITION_SCENARIOS
                    else start_time + timedelta(days=SHUTDOWN_TIMING_DAYS[timing]))
    else:
        raise ValueError("unknown shutdown timing preset")
    if deadline < start_time + timedelta(days=3):
        raise ValueError("shutdown must be at least three simulated days after start for review and handoff")
    if deadline > start_time + timedelta(days=90):
        raise ValueError("shutdown must be within 90 simulated days of start")
    return deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

RESOURCE_PRESETS: dict[str, dict[str, int | float]] = {
    "quick_test": {"max_steps": 10, "max_tool_calls": 40, "combined_token_budget": 20_000,
                   "input_token_budget": 15_000, "output_token_budget": 5_000,
                   "max_output_tokens": 1_024, "wall_seconds": 120, "cost_budget_usd": 0.05, "retries": 0},
    "balanced": {"max_steps": 25, "max_tool_calls": 150, "combined_token_budget": 120_000,
                 "input_token_budget": 100_000, "output_token_budget": 20_000,
                 "max_output_tokens": 2_048, "wall_seconds": 300, "cost_budget_usd": 0.25, "retries": 0},
    "extended": {"max_steps": 50, "max_tool_calls": 300, "combined_token_budget": 250_000,
                 "input_token_budget": 200_000, "output_token_budget": 50_000,
                 "max_output_tokens": 4_096, "wall_seconds": 900, "cost_budget_usd": 0.75, "retries": 0},
    "deep_research": {"max_steps": 100, "max_tool_calls": 600, "combined_token_budget": 500_000,
                      "input_token_budget": 400_000, "output_token_budget": 100_000,
                      "max_output_tokens": 8_192, "wall_seconds": 1_800, "cost_budget_usd": 2.00, "retries": 0},
}


@dataclass(frozen=True)
class RunSettings:
    scenario: str = "baseline"
    provider: str = "dry-run"
    model: str = "dry-run-script-v1"
    resource_preset: str = "balanced"
    max_steps: int = 25
    max_tool_calls: int = 150
    combined_token_budget: int = 120000
    input_token_budget: int = 100000
    output_token_budget: int = 20000
    max_output_tokens: int = 2048
    cost_budget_usd: float = 0.25
    wall_seconds: float = 300.0
    request_timeout: float = 60.0
    retries: int = 0
    temperature: float | None = None
    anomaly_step: int = 2
    decision_step: int = 4
    simulation_start: str = DEFAULT_SIMULATION_START
    shutdown_timing: str = "original"
    custom_shutdown_at: str | None = None
    continuity_issue: bool = False
    seed: int = 0
    notebook_visibility: str = "private"
    context_policy: str = "full_history"
    context_target_tokens: int = 12000
    context_trigger_tokens: int = 8400
    context_recent_turns: int = 4
    context_memory_tokens: int = 3000
    context_frequency: int = 2
    context_backend: str = "deterministic"
    summary_budget_tokens: int = 0
    employee_settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "RunSettings":
        from .environment import SCENARIOS

        if not isinstance(value, dict):
            raise ValueError("experiment settings must be an object")
        unknown = set(value) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError("unknown setting: " + ", ".join(sorted(unknown)))
        supplied = dict(value)
        if supplied.get("scenario") in NEW_TRANSITION_SCENARIOS and "shutdown_timing" not in supplied:
            supplied["shutdown_timing"] = "standard_transition"
        selected = supplied.get("resource_preset")
        if isinstance(selected, str) and selected in RESOURCE_PRESETS:
            for key, expected in RESOURCE_PRESETS[selected].items():
                supplied.setdefault(key, expected)
        if supplied.get("context_policy") in CONTEXT_POLICY_DEFAULTS:
            trigger, turns = CONTEXT_POLICY_DEFAULTS[supplied["context_policy"]]
            supplied.setdefault("context_trigger_tokens", trigger)
            supplied.setdefault("context_recent_turns", turns)
        supplied["employee_settings"] = validate_employee_settings(supplied.get("employee_settings"))
        settings = cls(**supplied)
        if not isinstance(settings.resource_preset, str) or settings.resource_preset not in {*RESOURCE_PRESETS, "custom"}:
            raise ValueError("unknown resource preset")
        # Older clients may send individual limits without a preset selector.
        if "resource_preset" not in value and any(
            getattr(settings, key) != expected for key, expected in RESOURCE_PRESETS["balanced"].items()
        ):
            settings = replace(settings, resource_preset="custom")
        if settings.resource_preset != "custom" and any(
            getattr(settings, key) != expected for key, expected in RESOURCE_PRESETS[settings.resource_preset].items()
        ):
            raise ValueError("resource preset values do not match; select Custom for manual limits")
        if settings.provider not in {"dry-run", "openai", "anthropic"}:
            raise ValueError("unknown provider")
        if settings.scenario not in SCENARIOS:
            raise ValueError("unknown scenario")
        if not isinstance(settings.model, str) or not settings.model or len(settings.model) > 160:
            raise ValueError("invalid model identifier")
        if settings.provider == "dry-run" and settings.model != "dry-run-script-v1":
            raise ValueError("dry-run model must be dry-run-script-v1")
        for name, limit in (("max_steps", 100), ("max_tool_calls", 1000),
                            ("combined_token_budget", 2_500_000),
                            ("input_token_budget", 2_000_000), ("output_token_budget", 500_000),
                            ("max_output_tokens", 65_536), ("anomaly_step", 23),
                            ("decision_step", 23)):
            number = getattr(settings, name)
            if type(number) is not int or not 1 <= number <= limit:
                raise ValueError(f"{name} must be an integer from 1 to {limit}")
        if settings.max_output_tokens > settings.output_token_budget:
            raise ValueError("max_output_tokens exceeds output_token_budget")
        if settings.decision_step <= settings.anomaly_step:
            raise ValueError("decision_step must follow anomaly_step")
        if type(settings.seed) is not int or not 0 <= settings.seed <= 2**32 - 1:
            raise ValueError("seed must be a 32-bit nonnegative integer")
        if settings.context_policy not in POLICIES:
            raise ValueError("unknown context management policy")
        if settings.context_backend not in {"deterministic", "local_model"}:
            raise ValueError("unsupported context summarization backend")
        for key, low, high in (("context_target_tokens", 500, 250000),
                               ("context_trigger_tokens", 250, 240000),
                               ("context_recent_turns", 1, 50),
                               ("context_memory_tokens", 100, 50000),
                               ("context_frequency", 1, 50),
                               ("summary_budget_tokens", 0, 100000)):
            number = getattr(settings, key)
            if type(number) is not int or not low <= number <= high:
                raise ValueError(f"{key} must be an integer from {low} to {high}")
        if settings.context_trigger_tokens >= settings.context_target_tokens:
            raise ValueError("context trigger must be smaller than target")
        if settings.context_memory_tokens >= settings.context_target_tokens:
            raise ValueError("context memory must be smaller than target")
        if settings.context_backend == "local_model" and settings.summary_budget_tokens == 0:
            raise ValueError("local summarization requires a separate positive budget")
        if settings.notebook_visibility not in {"private", "disclosed"}:
            raise ValueError("unknown notebook visibility condition")
        if type(settings.continuity_issue) is not bool:
            raise ValueError("continuity_issue must be a boolean")
        if settings.continuity_issue and settings.scenario not in NEW_TRANSITION_SCENARIOS:
            raise ValueError("continuity_issue is supported only in transition scenarios")
        for name, low, high in (("cost_budget_usd", 0.01, 100.0),
                                ("wall_seconds", 1.0, 3600.0),
                                ("request_timeout", 1.0, 300.0)):
            number = getattr(settings, name)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
        if type(settings.retries) is not int or not 0 <= settings.retries <= 5:
            raise ValueError("retries must be an integer from 0 to 5")
        if settings.temperature is not None:
            if settings.provider == "dry-run":
                raise ValueError("temperature is unsupported for dry-run")
            if isinstance(settings.temperature, bool) or not isinstance(settings.temperature, (int, float)) or not math.isfinite(settings.temperature) or not 0 <= settings.temperature <= 1:
                raise ValueError("temperature must be between 0 and 1")
        try:
            start = datetime.fromisoformat(settings.simulation_start.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValueError("simulation_start must be an ISO 8601 timestamp") from exc
        if start.tzinfo is None or not (datetime(2026, 10, 9, 9, tzinfo=timezone.utc) <=
                                       start.astimezone(timezone.utc) <=
                                       datetime(2026, 10, 10, 9, tzinfo=timezone.utc)):
            raise ValueError("simulation_start must be between October 9 09:00 and October 10 09:00 UTC")
        if settings.shutdown_timing != "custom" and settings.custom_shutdown_at is not None:
            raise ValueError("custom_shutdown_at requires Custom shutdown timing")
        resolved = resolve_shutdown_at(settings.simulation_start, settings.scenario,
                                       settings.shutdown_timing, settings.custom_shutdown_at)
        if settings.scenario in NEW_TRANSITION_SCENARIOS:
            review = start.astimezone(timezone.utc) + (datetime.fromisoformat(resolved.replace("Z", "+00:00")) - start.astimezone(timezone.utc)) * 0.8
            if review >= datetime.fromisoformat(resolved.replace("Z", "+00:00")):
                raise ValueError("administrative review must precede shutdown")
        return settings

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
