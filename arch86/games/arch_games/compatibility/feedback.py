"""Local user attestations bound to machine, installation and launch configuration."""

from datetime import datetime, timezone
from pathlib import Path
import uuid

from ..config import AppError
from .analyzer import fingerprint
from .executables import inspect_file

RESULTS = {
    "works": "Works perfectly",
    "issues": "Works with issues",
    "slow": "Slow",
    "fails": "Doesn't launch",
    "untested": "Untested",
}


def file_identity(path):
    try:
        info = Path(path).stat()
        return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "inode": info.st_ino}
    except OSError:
        return None


def prefix_identity(path):
    if not path or not Path(path).is_dir():
        return None
    from ..config import read_json

    marker = read_json(Path(path) / ".arch-games-prefix.json")
    return {
        "instance_id": marker.get("instance_id"),
        "system": file_identity(Path(path) / "system.reg"),
        "user": file_identity(Path(path) / "user.reg"),
    }


def latest_launch(db, game_id):
    records = [r["value"] for r in db.all("launches") if r["value"].get("game_id") == game_id]
    return max(records, key=lambda r: r.get("started_at", ""), default=None)


def save_feedback(db, game_id, result, profile, notes="", launch_id=None, manual=False):
    if result not in RESULTS:
        raise AppError("Unknown feedback result")
    if len(notes) > 8192:
        raise AppError("Feedback notes exceed 8192 characters")
    launch = db.get("launches", launch_id) if launch_id else latest_launch(db, game_id)
    if launch_id and (not launch or launch.get("game_id") != game_id):
        raise AppError("Launch ID does not belong to this game")
    if result in {"works", "issues"} and not manual and (not launch or launch.get("state") != "exited"):
        raise AppError(
            "Successful feedback needs a completed local launch, or --manual for an explicit user attestation"
        )
    machine = fingerprint(profile)
    if launch and launch.get("fingerprint") != machine and result in {"works", "issues"}:
        raise AppError("Machine/runtime changed since this launch; retest before marking verified")
    install = db.get("installs", game_id, {})
    path = launch.get("executable") if launch else None
    record = {
        "game_id": game_id,
        "result": result,
        "label": RESULTS[result],
        "notes": notes,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": machine,
        "architecture": profile.get("os_architecture"),
        "wine_version": profile.get("wine", {}).get("version"),
        "graphics": profile.get("graphics"),
        "archive_sha256": install.get("archive_sha256"),
        "transaction": install.get("transaction"),
        "launch_id": launch.get("id") if launch else None,
        "runtime_configuration": {
            k: launch.get(k) for k in ["method", "argv", "prefix", "runtime_fingerprint"]
        }
        if launch
        else {"source": "manual user attestation"},
        "prefix_path": launch.get("prefix_path") if launch else None,
        "prefix_identity": prefix_identity(launch.get("prefix_path")) if launch else None,
        "executable": path,
        "file_identity": file_identity(path) if path else None,
        "binary": inspect_file(path).to_dict() if path and Path(path).is_file() else None,
        "manual": manual,
        "synchronized": False,
    }
    db.put("feedback", game_id, record)
    db.put("feedback_history", uuid.uuid4().hex, record)
    return record


def current_feedback(db, game_id):
    record = db.get("feedback", game_id)
    if not record:
        return None
    install = db.get("installs", game_id, {})
    if record.get("transaction") != install.get("transaction") or record.get("archive_sha256") != install.get(
        "archive_sha256"
    ):
        return None
    if record.get("executable") and file_identity(record["executable"]) != record.get("file_identity"):
        return None
    if record.get("prefix_path") and prefix_identity(record["prefix_path"]) != record.get("prefix_identity"):
        return None
    launch = latest_launch(db, game_id)
    if launch and record.get("launch_id") != launch.get("id"):
        return None
    return record
