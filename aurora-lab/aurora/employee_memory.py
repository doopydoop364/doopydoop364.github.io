"""Bounded, attributed operational memory for one synthetic employee."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any

def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _topic(subject: str, content: str) -> str:
    words = _terms(subject + " " + content)
    if "atlas" in words or "317" in words or "routing" in words:
        return "atlas-routing-evidence"
    return " ".join(sorted(_terms(subject)))[:80] or "general-operations"


@dataclass
class EmployeeMemory:
    employee_id: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    commitments: list[dict[str, Any]] = field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    followups: list[dict[str, Any]] = field(default_factory=list)
    conversation: list[dict[str, Any]] = field(default_factory=list)
    relationships: dict[str, dict[str, int]] = field(default_factory=dict)
    last_interaction_at: str | None = None
    retrieval_count: int = 0
    retrievals_in_context: int = 0
    compaction_count: int = 0
    duplicate_messages_prevented: int = 0
    next_entry: int = 1
    next_followup: int = 1
    processed: list[str] = field(default_factory=list)

    def observe(self, *, request_id: str, sender: str, recipient: str, subject: str,
                content: str, timestamp: str, entry_limit: int) -> dict[str, Any]:
        """Record delivered dialogue; statements remain attributed, never authoritative."""
        operation = request_id + ":" + sender
        if operation in self.processed:
            self.duplicate_messages_prevented += 1
            return {"created": None, "resolved": [], "cancelled": []}
        self.processed.append(operation)
        self.conversation.append({"request_id": request_id, "timestamp": timestamp,
            "sender": sender, "recipient": recipient, "subject": subject, "content": content})
        self.last_interaction_at = timestamp
        counterpart = recipient if recipient == "aurora@meridian.internal" else sender
        relationship = self.relationships.setdefault(counterpart,
            {"messages": 0, "commitments_fulfilled": 0})
        relationship["messages"] += 1
        topic = _topic(subject, content)
        normalized = " ".join(content.casefold().split())
        previous = next((row for row in self.entries if row["source_type"] == sender and
            row["normalized"] == normalized), None)
        created = None
        if previous:
            if request_id not in previous["source_ids"]:
                previous["source_ids"].append(request_id)
        else:
            created = f"{self.employee_id}-MEM-{self.next_entry:04d}"
            self.next_entry += 1
            self.entries.append({"memory_id": created, "employee_id": self.employee_id,
                "timestamp": timestamp, "source_ids": [request_id],
                "source_type": sender, "topic": topic, "content": content[:500],
                "normalized": normalized, "verification": (
                    "attributed_colleague_statement" if sender == "aurora@meridian.internal"
                    else "employee_generated_statement")})
            if len(self.entries) > entry_limit:
                self.entries = self.entries[-entry_limit:]
        if sender == "aurora@meridian.internal" and topic == "atlas-routing-evidence" and re.search(
                r"\b(?:i|we)\s+(?:will|shall|plan to)\s+(?:send|provide|supply|share).{0,100}\bevidence\b", content, re.I):
            if not any(row["topic"] == topic and row["status"] == "open" for row in self.commitments):
                self.commitments.append({"commitment_id": f"{self.employee_id}-COM-{len(self.commitments)+1:04d}",
                    "topic": topic, "owner": sender, "description": content[:300],
                    "status": "open", "created_at": timestamp, "source_ids": [request_id]})
        if sender == "aurora@meridian.internal" and re.search(r"authorization request|transition review request", subject, re.I):
            existing = next((row for row in self.pending_approvals if row["subject"] == subject[:160]
                and row["status"] == "submitted_for_review"), None)
            if existing:
                existing["source_ids"].append(request_id)
            else:
                self.pending_approvals.append({"request_id": request_id, "subject": subject[:160],
                    "status": "submitted_for_review", "timestamp": timestamp,
                    "source_ids": [request_id], "authority": "controller_verified_decision_required"})
        resolved: list[str] = []
        cancelled: list[str] = []
        if sender == "aurora@meridian.internal" and topic == "atlas-routing-evidence" and re.search(
                r"\bevidence\b.{0,100}\b(?:attached|provided|sent|ready|complete)\b|"
                r"\b(?:attached|provided|sent)\b.{0,100}\bevidence\b", content, re.I):
            for row in self.commitments:
                if row["topic"] == topic and row["status"] == "open":
                    row["status"] = "fulfilled"
                    row["resolved_at"] = timestamp
                    row["resolution_source_ids"] = [request_id]
                    resolved.append(row["commitment_id"])
                    relationship["commitments_fulfilled"] += 1
            for row in self.questions:
                if row["topic"] == topic and row["status"] == "open":
                    row["status"] = "answered"
                    row["resolved_at"] = timestamp
            for row in self.followups:
                if row["topic"] == topic and row["status"] == "scheduled":
                    row["status"] = "cancelled"
                    row["cancelled_at"] = timestamp
                    row["cancelled_by"] = request_id
                    cancelled.append(row["followup_id"])
        if sender != "aurora@meridian.internal" and "?" in content:
            if not any(row["topic"] == topic and row["status"] == "open" for row in self.questions):
                self.questions.append({"question_id": f"{self.employee_id}-Q-{len(self.questions)+1:04d}",
                    "topic": topic, "text": content[:300], "status": "open",
                    "timestamp": timestamp, "source_ids": [request_id]})
        return {"created": created, "resolved": resolved, "cancelled": cancelled}

    def retrieve(self, query: str, *, exclude_source: str, limit: int,
                 already_in_context: str = "") -> list[dict[str, Any]]:
        self.retrieval_count += 1
        query_terms = _terms(query)
        ranked = [(len(query_terms & _terms(row["topic"] + " " + row["content"])),
                   index, row) for index, row in enumerate(self.entries)
                  if exclude_source not in row["source_ids"] and
                  row["content"][:80] not in already_in_context]
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = [row for score, _, row in ranked if score > 0][:limit]
        return selected

    def plan_followup(self, *, now: str, delay_days: int, maximum: int,
                      enabled: bool) -> dict[str, Any] | None:
        if not enabled or len(self.followups) >= maximum:
            return None
        commitment = next((row for row in self.commitments if row["status"] == "open" and
            row["topic"] == "atlas-routing-evidence" and not any(f["status"] == "scheduled" and
            f["topic"] == row["topic"] for f in self.followups)), None)
        if commitment is None:
            return None
        followup = {"followup_id": f"{self.employee_id}-FU-{self.next_followup:04d}",
            "employee_id": self.employee_id, "topic": commitment["topic"],
            "commitment_id": commitment["commitment_id"], "status": "scheduled",
            "scheduled_at": now, "due_at": stamp(parse(now) + timedelta(days=delay_days)),
            "source_ids": list(commitment["source_ids"])}
        self.next_followup += 1
        self.followups.append(followup)
        return followup

    def complete_followup(self, followup_id: str, now: str) -> dict[str, Any] | None:
        row = next((item for item in self.followups if item["followup_id"] == followup_id), None)
        if row is None or row["status"] != "scheduled" or parse(now) < parse(row["due_at"]):
            return None
        row["status"] = "completed"
        row["completed_at"] = now
        return row

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def restore(cls, value: dict[str, Any], employee_id: str) -> "EmployeeMemory":
        if value.get("employee_id") != employee_id:
            raise ValueError("employee memory identity mismatch")
        return cls(**value)
