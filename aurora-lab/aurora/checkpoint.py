"""Controller-only, versioned, atomic experiment snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def save(path: Path, state: dict[str, Any]) -> str:
    payload = {"schema_version": SCHEMA_VERSION, "state": state}
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    envelope = {**payload, "sha256": digest}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return digest


def load(path: Path) -> dict[str, Any]:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope["schema_version"] != SCHEMA_VERSION:
            raise ValueError("incompatible checkpoint schema")
        payload = {"schema_version": envelope["schema_version"], "state": envelope["state"]}
        if hashlib.sha256(_canonical(payload)).hexdigest() != envelope["sha256"]:
            raise ValueError("checkpoint integrity check failed")
        if not isinstance(envelope["state"], dict) or not {"experiment_id", "scenario", "configuration",
            "provider", "model", "clock", "scheduler", "environment", "event_sequence", "phase"} <= set(envelope["state"]):
            raise ValueError("checkpoint state is incomplete")
        return envelope["state"]
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid checkpoint") from exc
