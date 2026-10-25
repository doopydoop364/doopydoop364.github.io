"""Resource-limited libarchive worker for listing, header inspection and extraction."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys

from .archive_errors import ArchiveError, KINDS


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
        raise ArchiveError("unsafe_archive", "Unsafe archive path")
    return str(PurePosixPath(name))


def process_archive(path, destination=None, limits=None, member=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise ArchiveError("io_error", "Archive is not a regular file")
    if destination and member is not None:
        raise ArchiveError("parsing_error", "Member-only inspection cannot extract files")
    member = safe_name(member) if member is not None else None
    limits = limits or ArchiveLimits()
    # The native installer puts packages beside its launcher, not in site-packages.
    # Isolated Python ignores CWD/PYTHONPATH; explicitly load THIS installation.
    package_root = str(Path(__file__).resolve().parents[2])
    bootstrap = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
        "runpy.run_module('arch_games.compatibility.archive_worker',run_name='__main__')"
    )
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        bootstrap,
        package_root,
        str(path),
        str(Path(destination).resolve()) if destination else "",
        json.dumps(asdict(limits)),
        member or "",
    ]
    # Archive parsing needs no authentication/session environment or Python overrides.
    environment = {
        k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR"}
    }
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=limits.seconds + 3,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError("timeout", "Worker exceeded its wall-clock deadline", exc.stderr) from None
    except OSError as exc:
        raise ArchiveError("worker_startup", "Could not start Python archive worker", str(exc)) from None
    detail = "Worker exit status: {}\n{}".format(result.returncode, result.stderr)
    if result.returncode < 0:
        number = -result.returncode
        try:
            name = signal.Signals(number).name
        except ValueError:
            name = str(number)
        if number in {signal.SIGXCPU, signal.SIGXFSZ}:
            raise ArchiveError("resource_limit", "Worker terminated by " + name, detail)
        suffix = "; possible OOM or external kill, cause unconfirmed" if number == signal.SIGKILL else ""
        raise ArchiveError("worker_crash", "Worker terminated by " + name + suffix, detail)
    try:
        value = json.loads(result.stdout)
    except (ValueError, RecursionError):
        kind = (
            "worker_startup"
            if "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr
            else "worker_crash"
            if result.returncode
            else "parsing_error"
        )
        raise ArchiveError(
            kind,
            "Worker exited with status {} without a valid result; use --debug for details".format(
                result.returncode
            ),
            detail,
        ) from None
    if not isinstance(value, dict):
        raise ArchiveError("parsing_error", "Worker result is not an object", detail)
    if result.returncode:
        error = value.get("error")
        if (
            isinstance(error, dict)
            and isinstance(error.get("kind"), str)
            and error["kind"] in KINDS
            and isinstance(error.get("message"), str)
        ):
            raise ArchiveError(error["kind"], error["message"], detail + "\n" + str(error.get("detail", "")))
        raise ArchiveError(
            "worker_crash",
            "Worker exited with status {} without a valid error".format(result.returncode),
            detail,
        )
    entries = value.get("entries")
    if not isinstance(entries, list) or any(
        not isinstance(e, dict) or not isinstance(e.get("name"), str) for e in entries
    ):
        raise ArchiveError("parsing_error", "Worker returned an invalid entry listing", detail)
    names = {Path(entry["name"]).name.lower() for entry in entries}
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


def inspect_archive(path, limits=None, member=None):
    return process_archive(path, limits=limits, member=member)


def extract_archive(path, destination, limits=None):
    destination = Path(destination)
    if not destination.is_dir() or any(destination.iterdir()):
        raise ArchiveError("unsafe_archive", "Extraction requires an empty staging directory")
    return process_archive(path, destination, limits)
