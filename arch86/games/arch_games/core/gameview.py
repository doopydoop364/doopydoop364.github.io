"""One structured description of a game, shared by the CLI, TUI and web UI (no presentation here)."""

import os
from pathlib import Path

from ..config import AppError


def dir_size(path, limit=20000):
    total = count = 0
    for root, _, names in os.walk(path, followlinks=False):
        for name in names:
            count += 1
            if count > limit:
                return total, True
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total, False


def describe(config, db, game, report, aliases=(), favorite=False, with_storage=True):
    launcher = db.get("launchers", game.id)
    prefix = db.get("prefixes", game.id)
    manifest = db.get("installs", game.id)
    job = db.get("install_jobs", game.id)
    installed = bool(game.installed or launcher or prefix)
    launches = [v for v in db.mapping("launches").values() if v.get("game_id") == game.id and v.get("purpose", "game") == "game"]
    last = max(launches, key=lambda v: v.get("started_at", ""), default=None)
    storage = None
    if with_storage and manifest:
        try:
            size, partial = dir_size(config.directory("installed") / manifest["directory"])
            storage = {"bytes": size, "partial": partial}
        except AppError:
            pass
    return {
        "id": game.id,
        "title": game.title,
        "source": game.source,
        "category": game.category,
        "description": game.description,
        "os": game.os,
        "architecture": game.architecture,
        "installed": installed,
        "favorite": bool(favorite),
        "aliases": list(aliases),
        "cover": ((game.metadata.get("remote") or {}).get("metadata") or {}).get("cover", {}).get("id"),
        "compatibility": {
            "status": report.get("status", "Unknown"),  # legacy 2.0 label, kept for scripts
            "classification": report.get("classification", "Unknown"),
            "reasons": list(report.get("classification_reasons") or []),
            "facts": report.get("facts"),
            "evidence": list(report.get("evidence") or []),
            "uncertainties": list(report.get("uncertainties") or []),
            "runtime": report.get("runtime"),
        },
        "prefix": {k: prefix.get(k) for k in ("state", "requested_architecture", "detected_architecture")} if prefix else None,
        "wine_settings": db.get("game_settings", game.id) or None,
        "launcher": ({"executable": launcher["executable"], "exists": Path(launcher["executable"]).is_file(), "runner": launcher["runner"], "confirmed": launcher.get("confirmed", False)} if launcher else None),
        "last_launch": ({k: last.get(k) for k in ("started_at", "state", "exit_code")} if last else None),
        "install_job": ({"status": job.get("status"), "step": job.get("step"), "error": job.get("error")} if job and job.get("status") not in {"done", "cancelled"} else None),
        "storage": storage,
        "notes": (db.get("notes", game.id) or {}).get("text"),
        "tags": (db.get("tags", game.id) or {}).get("tags", []),
    }
