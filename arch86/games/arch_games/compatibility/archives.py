"""Resource-limited libarchive worker for listing, header inspection and extraction."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys

from ..config import AppError


@dataclass
class ArchiveLimits:
    files: int = 10000
    total_bytes: int = 32 * 1024**3
    member_bytes: int = 8 * 1024**3
    ratio: int = 1000
    seconds: int = 30
    memory_bytes: int = 384 * 1024**2


def safe_name(name):
    name = name.replace("\\", "/").rstrip("/")
    parts = PurePosixPath(name).parts
    if (
        not name
        or len(name) > 1024
        or name.startswith("/")
        or len(parts) > 32
        or any(p in {"..", "."} or ":" in p for p in name.split("/"))
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise AppError("Unsafe archive path")
    return str(PurePosixPath(name))


def process_archive(path, destination=None, limits=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise AppError("Archive is not a regular file")
    limits = limits or ArchiveLimits()
    command = [
        sys.executable,
        "-m",
        "arch_games.compatibility.archive_worker",
        str(path),
        str(Path(destination).resolve()) if destination else "",
        json.dumps(asdict(limits)),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=limits.seconds + 3)
    except subprocess.TimeoutExpired:
        raise AppError("Archive inspection exceeded time limit") from None
    if result.returncode:
        try:
            error = json.loads(result.stdout).get("error", "Archive worker failed or exceeded resources")
        except ValueError:
            error = "Archive worker failed or exceeded resources"
        raise AppError(error)
    try:
        value = json.loads(result.stdout)
        names = {Path(entry["name"]).name.lower() for entry in value.get("entries", [])}
        clues = []
        for engine, markers in [
            ("Unity", {"unityplayer.dll", "globalgamemanagers"}),
            ("RenPy", {"renpy"}),
            ("RPG Maker", {"rpg_rt.exe", "game.rgss3a", "rpg_core.js"}),
        ]:
            if names & markers:
                clues.append(engine + ": archive filename hint, not verified engine identification")
        if any(name.endswith(".pck") for name in names):
            clues.append("Godot: .pck filename hint, not verified engine identification")
        value["engine_clues"] = clues
        return value
    except ValueError:
        raise AppError("Invalid archive worker result") from None


def inspect_archive(path, limits=None):
    return process_archive(path, limits=limits)


def extract_archive(path, destination, limits=None):
    destination = Path(destination)
    if not destination.is_dir() or any(destination.iterdir()):
        raise AppError("Extraction requires an empty staging directory")
    return process_archive(path, destination, limits)
