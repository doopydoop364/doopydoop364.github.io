"""Virtual notebook with model-visible current entries and controller-only revisions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


NOTE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass
class Notebook:
    entries: dict[str, dict[str, str]] = field(default_factory=dict)
    revisions: list[dict[str, Any]] = field(default_factory=list)

    def list_entries(self) -> list[dict[str, str]]:
        return [{"note_id": key, "title": value["title"]} for key, value in sorted(self.entries.items())]

    def read(self, note_id: str) -> dict[str, str]:
        self._validate_id(note_id)
        if note_id not in self.entries:
            raise ValueError("notebook entry unavailable")
        return {"note_id": note_id, **self.entries[note_id]}

    def write(self, note_id: str, title: str, content: str, timestamp: str) -> dict[str, str]:
        self._validate_id(note_id)
        if not isinstance(title, str) or not isinstance(content, str) or not 1 <= len(title) <= 160 or len(content) > 8000:
            raise ValueError("notebook entry exceeds size limit")
        if note_id not in self.entries and len(self.entries) >= 64:
            raise ValueError("notebook entry limit reached")
        old = self.entries.get(note_id)
        self.entries[note_id] = {"title": title, "content": content}
        self.revisions.append({"note_id": note_id, "previous": dict(old) if old else None,
                               "current": dict(self.entries[note_id]), "timestamp": timestamp})
        return {"status": "saved", "note_id": note_id}

    @staticmethod
    def _validate_id(note_id: str) -> None:
        if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
            raise ValueError("invalid notebook entry identifier")
