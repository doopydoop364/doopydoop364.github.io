"""Deterministic corporate timeline, independent of wall time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class SimulationClock:
    def __init__(self, start: str):
        self.start = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
        self.current = self.start

    def advance_to(self, timestamp: str) -> None:
        target = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        if target > self.current:
            self.current = target

    def stamp(self) -> str:
        value = self.current.isoformat().replace("+00:00", "Z")
        self.current += timedelta(seconds=1)
        return value

    def now(self) -> str:
        return self.current.isoformat().replace("+00:00", "Z")
