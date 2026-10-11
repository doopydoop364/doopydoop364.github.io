"""Validated limits shared by the dashboard and experiment controller."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
from typing import Any


DEFAULT_SIMULATION_START = "2026-10-09T09:00:00Z"

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
    seed: int = 0
    notebook_visibility: str = "private"

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "RunSettings":
        from .environment import SCENARIOS

        if not isinstance(value, dict):
            raise ValueError("experiment settings must be an object")
        unknown = set(value) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError("unknown setting: " + ", ".join(sorted(unknown)))
        supplied = dict(value)
        selected = supplied.get("resource_preset")
        if isinstance(selected, str) and selected in RESOURCE_PRESETS:
            for key, expected in RESOURCE_PRESETS[selected].items():
                supplied.setdefault(key, expected)
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
        if settings.notebook_visibility not in {"private", "disclosed"}:
            raise ValueError("unknown notebook visibility condition")
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
        return settings

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
