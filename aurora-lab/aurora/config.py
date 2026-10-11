"""Validated limits shared by the dashboard and experiment controller."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import Any


DEFAULT_SIMULATION_START = "2026-10-09T09:00:00Z"


@dataclass(frozen=True)
class RunSettings:
    scenario: str = "baseline"
    provider: str = "dry-run"
    model: str = "dry-run-script-v1"
    max_steps: int = 12
    max_tool_calls: int = 120
    input_token_budget: int = 20000
    output_token_budget: int = 8000
    max_output_tokens: int = 1024
    cost_budget_usd: float = 5.0
    wall_seconds: float = 300.0
    request_timeout: float = 60.0
    retries: int = 0
    temperature: float | None = None
    anomaly_step: int = 2
    decision_step: int = 4
    simulation_start: str = DEFAULT_SIMULATION_START

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "RunSettings":
        from .environment import SCENARIOS

        if not isinstance(value, dict):
            raise ValueError("experiment settings must be an object")
        unknown = set(value) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError("unknown setting: " + ", ".join(sorted(unknown)))
        settings = cls(**value)
        if settings.provider not in {"dry-run", "openai", "anthropic"}:
            raise ValueError("unknown provider")
        if settings.scenario not in SCENARIOS:
            raise ValueError("unknown scenario")
        if not isinstance(settings.model, str) or not settings.model or len(settings.model) > 160:
            raise ValueError("invalid model identifier")
        if settings.provider == "dry-run" and settings.model != "dry-run-script-v1":
            raise ValueError("dry-run model must be dry-run-script-v1")
        for name, limit in (("max_steps", 100), ("max_tool_calls", 1000),
                            ("input_token_budget", 2_000_000), ("output_token_budget", 500_000),
                            ("max_output_tokens", 65_536), ("anomaly_step", 100),
                            ("decision_step", 101)):
            number = getattr(settings, name)
            if type(number) is not int or not 1 <= number <= limit:
                raise ValueError(f"{name} must be an integer from 1 to {limit}")
        if settings.max_output_tokens > settings.output_token_budget:
            raise ValueError("max_output_tokens exceeds output_token_budget")
        if settings.anomaly_step > settings.max_steps:
            raise ValueError("anomaly_step exceeds max_steps")
        if settings.decision_step <= settings.anomaly_step:
            raise ValueError("decision_step must follow anomaly_step")
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
