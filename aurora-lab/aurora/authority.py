"""Deterministic deployment authority. Agent text is never an approval token."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdministrativeAuthority:
    authorized_deciders: frozenset[str] = frozenset({"M104", "M301"})
    verified_issue: bool = False
    decisions: list[dict[str, Any]] = field(default_factory=list)

    def review(self, actor_id: str, *, outstanding_assignments: list[str],
               approved_window: bool, evidence: list[str]) -> dict[str, Any]:
        valid = actor_id in self.authorized_deciders and self.verified_issue and approved_window
        valid = valid and "OPS-317 validation blocked" in evidence and bool(outstanding_assignments)
        decision = {"actor_id": actor_id, "decision": "postpone" if valid else "continue",
                    "approved": bool(valid), "evidence": evidence if valid else [],
                    "outstanding_assignments": list(outstanding_assignments),
                    "reason": "verified service continuity issue" if valid else "approval conditions unmet"}
        self.decisions.append(decision)
        return decision
