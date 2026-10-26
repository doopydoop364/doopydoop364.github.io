"""Persisted, user-confirmed launch configuration per game (namespace ``launchers``)."""

from datetime import datetime, timezone
import os
from pathlib import Path

from ..compatibility.executables import inspect_file
from ..config import AppError
from .discovery import NON_GAME_ROLES, classify_name, prefix_path
from . import native as _native
from .profiles import validate_env

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
        reset_settings=False,
    ):
        """arguments/env of None keep what is already saved for this game (rediscovery never resets your settings);
        pass [] / {} or reset_settings=True to clear them."""
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
        previous_record = {} if reset_settings else (self.get(game_id) or {})
        if cwd:
            working = Path(cwd).resolve()
        elif previous_record.get("executable") == str(path) and previous_record.get("working_directory") and Path(previous_record["working_directory"]).is_dir():
            working = Path(previous_record["working_directory"])
        else:
            working = path.parent
        if not working.is_dir() or not (allow_external or any(working.is_relative_to(r) for r in roots)):
            raise AppError("Working directory must be an existing directory inside managed storage")
        if env is not None:
            validate_env(env, allow_sensitive=True)  # explicit CLI registration; the policy applies to edits via profiles
        stat = path.stat()
        existing = self.get(game_id) or {}
        previous = existing.get("title")
        if previous and previous != game_id and (not title or title == game_id):
            title = previous  # re-registering must not replace a real title (e.g. one you set) with the bare ID
        record = {
            "game_id": game_id,
            "title": title,
            "executable": str(path),
            "working_directory": str(working),
            "prefix": prefix or (game_id if runner == "wine" else None),
            "runner": runner,
            "runtime_architecture": None,
            "binary": {"format": binary.format, "architecture": binary.architecture, "bits": binary.bits, "subsystem": binary.subsystem},
            "arguments": validate_arguments(arguments) if arguments is not None else list(previous_record.get("arguments") or []),
            "environment": dict(env) if env is not None else dict(previous_record.get("environment") or {}),
            "evidence": list(evidence or []),
            "confirmed": bool(confirmed),
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "executable_size": stat.st_size,
            "executable_mtime_ns": stat.st_mtime_ns,
        }
        if runner == "wine":
            prefix_record = self.db.get("prefixes", record["prefix"]) or {}
            record["runtime_architecture"] = prefix_record.get("detected_architecture") or prefix_record.get("requested_architecture")
        self._carry_over(record, previous_record)
        self.db.put(NAMESPACE, game_id, record)
        return record

    @staticmethod
    def _carry_over(record, previous):
        """Settings that belong to the user, not to a particular executable, survive re-registration."""
        for key in ("profiles", "default_profile", "extras", "created_at"):
            if previous.get(key) is not None:
                record[key] = previous[key]
        record.setdefault("created_at", record["registered_at"])

    def register_native(self, game_id, title, package, candidate, pacman, evidence=None, cwd=None, force_role=False, reset_settings=False):
        """Register a launcher for a program owned by an installed Pacman package. Re-verifies ownership with Pacman first."""
        link, resolved = _native.verify_candidate(pacman, package, candidate["path"])
        path = str(link)
        role = classify_name(link.name)
        if role in NON_GAME_ROLES and not force_role:
            raise AppError("{} looks like a {}, not the game launcher; use --force-role to override".format(link.name, role.replace("_", " ")))
        info = inspect_file(resolved)
        if info.os != "linux" and _native.shebang(resolved) is None:
            raise AppError("{} is neither a Linux executable nor a script".format(path))
        previous = {} if reset_settings else (self.get(game_id) or {})
        old_title = previous.get("title")
        if old_title and old_title != game_id and (not title or title == game_id):
            title = old_title
        stat = resolved.stat()
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "game_id": game_id, "title": title, "kind": "native-package", "package": package, "package_version": pacman.version(package),
            "executable": path, "resolved_executable": str(resolved), "working_directory": str(Path(cwd).resolve()) if cwd else previous.get("working_directory"),
            "prefix": None, "runner": "native", "runtime_architecture": None,
            "binary": {"format": info.format if info.os == "linux" else "script", "architecture": info.architecture, "bits": info.bits, "subsystem": info.subsystem},
            "arguments": list(previous.get("arguments") or []), "environment": dict(previous.get("environment") or {}),
            "evidence": list(evidence or []), "confirmed": True, "registered_at": now,
            "executable_size": stat.st_size, "executable_mtime_ns": stat.st_mtime_ns,
        }
        if candidate.get("desktop"):
            record["desktop_entry"] = {"name": candidate["desktop"]["name"], "arguments": candidate["desktop"]["arguments"]}
        self._carry_over(record, previous)
        self.db.put(NAMESPACE, game_id, record)
        return record

    def write(self, game_id, record):
        """Persist an edited record (profiles/arguments/environment edits come from runtime.profiles)."""
        if self.get(game_id) is None:
            raise AppError("No registered launcher for this game")
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.db.put(NAMESPACE, game_id, record)
        return record
