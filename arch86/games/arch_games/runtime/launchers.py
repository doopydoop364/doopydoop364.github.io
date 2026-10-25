"""Persisted, user-confirmed launch configuration per game (namespace ``launchers``)."""

from datetime import datetime, timezone
from pathlib import Path

from ..compatibility.executables import inspect_file
from ..config import AppError
from .discovery import NON_GAME_ROLES, classify_name, prefix_path
from .environment import environment

NAMESPACE = "launchers"


def validate_arguments(arguments):
    arguments = list(arguments or [])
    if len(arguments) > 64 or not all(isinstance(a, str) and "\0" not in a and len(a) < 4096 for a in arguments):
        raise AppError("Invalid launch arguments")
    return arguments


class LauncherStore:
    def __init__(self, config, db):
        self.config, self.db = config, db

    def get(self, game_id):
        return self.db.get(NAMESPACE, game_id)

    def all(self):
        return {row["key"]: row["value"] for row in self.db.all(NAMESPACE)}

    def remove(self, game_id):
        if self.get(game_id) is None:
            raise AppError("No registered launcher for this game")
        self.db.delete(NAMESPACE, game_id)

    def allowed_roots(self, game_id, record=None):
        roots = [self.config.directory("installed").resolve()]
        pp = prefix_path(self.config, self.db, game_id, (record or {}).get("prefix"))
        if pp:
            roots.append((pp / "drive_c").resolve())
        return roots

    def register(
        self,
        game_id,
        title,
        executable,
        prefix=None,
        arguments=None,
        env=None,
        cwd=None,
        evidence=None,
        confirmed=True,
        allow_external=False,
        force_role=False,
    ):
        path = Path(executable).expanduser()
        if not path.is_absolute():
            raise AppError("Launcher executable must be an absolute path")
        if path.is_symlink() or not path.is_file():
            raise AppError("Launcher executable is missing, a symlink or not a regular file")
        path = path.resolve()
        roots = self.allowed_roots(game_id, {"prefix": prefix})
        if not allow_external and not any(path.is_relative_to(r) for r in roots):
            raise AppError(
                "Executable is outside this game's managed install and Wine prefix; --allow-external overrides"
            )
        role = classify_name(path.name)
        if role in NON_GAME_ROLES and not force_role:
            raise AppError("{} looks like a {}, not the game launcher; use --force-role to override".format(path.name, role.replace("_", " ")))
        binary = inspect_file(path)
        if binary.os == "windows" and binary.role == "installer" and not force_role:
            raise AppError("{} has installer evidence; use --force-role to override".format(path.name))
        runner = {"windows": "wine", "linux": "native", "dos": "dosbox"}.get(binary.os)
        if not runner:
            raise AppError("Unrecognized executable format; cannot choose a runner")
        if runner == "wine" and not prefix_path(self.config, self.db, game_id, prefix):
            raise AppError("No Wine prefix recorded for this game; run 'prefix create' (or pass --prefix)")
        working = Path(cwd).resolve() if cwd else path.parent
        if not working.is_dir() or not (allow_external or any(working.is_relative_to(r) for r in roots)):
            raise AppError("Working directory must be an existing directory inside managed storage")
        environment(env)  # validates keys and values
        stat = path.stat()
        record = {
            "game_id": game_id,
            "title": title,
            "executable": str(path),
            "working_directory": str(working),
            "prefix": prefix or (game_id if runner == "wine" else None),
            "runner": runner,
            "runtime_architecture": None,
            "binary": {"format": binary.format, "architecture": binary.architecture, "bits": binary.bits, "subsystem": binary.subsystem},
            "arguments": validate_arguments(arguments),
            "environment": dict(env or {}),
            "evidence": list(evidence or []),
            "confirmed": bool(confirmed),
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "executable_size": stat.st_size,
            "executable_mtime_ns": stat.st_mtime_ns,
        }
        if runner == "wine":
            prefix_record = self.db.get("prefixes", record["prefix"]) or {}
            record["runtime_architecture"] = prefix_record.get("detected_architecture") or prefix_record.get("requested_architecture")
        self.db.put(NAMESPACE, game_id, record)
        return record
