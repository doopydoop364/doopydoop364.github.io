"""Launch-related services shared by the CLI, TUI and web GUI: discovery for native and Wine games, launcher registration,
status, validation and settings edits. Front ends call these; none of them executes the game."""

from pathlib import Path

from ..config import AppError
from . import native, profiles
from .discovery import confidence as wine_confidence, discover as discover_managed
from .launcher import Launcher
from .launchers import LauncherStore
from .pacman import PacmanService


def package_for(game_id):
    return native.package_of(game_id)


def discover(config, db, game_id, title="", pacman=None, extra_dirs=(), include_excluded=False):
    """Candidates for any game: package-aware for Pacman games, managed-install/Wine-prefix search otherwise."""
    package = package_for(game_id)
    if package:
        pacman = pacman or PacmanService()
        result = native.discover_native(pacman, package, title, include_excluded)
        result["runner"] = "native"
        result["notes"] = [] if result["installed"] else ["Package {} is not installed. Install it with: arch-games install {}".format(package, game_id)]
        if pacman.unavailable:
            result["notes"].append("Pacman could not be queried on this machine.")
        return result
    candidates = discover_managed(config, db, game_id, title, extra_dirs, include_excluded)
    return {"runner": "managed", "installed": None, "package": None, "version": None, "candidates": candidates,
            "confidence": wine_confidence(candidates), "notes": []}


def register(config, db, game_id, title, candidate, pacman=None, **options):
    package = package_for(game_id)
    store = LauncherStore(config, db)
    evidence = ["Discovery score {}".format(candidate.get("score"))] + list(candidate.get("evidence") or [])
    if package:
        return store.register_native(game_id, title, package, candidate, pacman or PacmanService(), evidence=evidence,
                                     cwd=options.get("cwd"), force_role=options.get("force_role", False),
                                     reset_settings=options.get("reset_settings", False))
    return store.register(game_id, title, candidate["path"], evidence=evidence, **options)


def status(config, db, game_id, pacman=None, deep=True):
    """{"status","label","detail","package","version","launcher"}: one vocabulary for the CLI, TUI and web GUI."""
    package = package_for(game_id)
    record = LauncherStore(config, db).get(game_id)
    if not package:
        if record is None:
            return {"status": native.NO_LAUNCHER if db.get("installs", game_id) else native.AVAILABLE, "label": "No launcher registered", "detail": "", "package": None, "version": None, "launcher": None}
        exists = Path(record["executable"]).is_file()
        return {"status": native.READY if exists else native.INVALID, "label": native.LABELS[native.READY if exists else native.INVALID],
                "detail": "" if exists else record["executable"] + " is missing", "package": None, "version": None, "launcher": record["executable"]}
    pacman = pacman or PacmanService()
    version = pacman.version(package)
    if record is not None and record.get("kind") == "native-package" and version is not None and deep:
        state, detail = native.verify_launcher(pacman, record)
    elif record is not None and record.get("kind") == "native-package":
        state = native.status(version is not None, version, record, Path(record["executable"]).exists())
        detail = ""
    else:
        state = native.status(version is not None, version, record, record is not None and Path(record["executable"]).is_file())
        detail = ""
    if record is not None and record.get("kind") != "native-package" and version is not None:
        state = native.READY if Path(record["executable"]).is_file() else native.INVALID
    return {"status": state, "label": native.LABELS[state], "detail": detail, "package": package, "version": version,
            "launcher": record["executable"] if record else None}


def validate(config, db, game_id, profile=None, pacman=None, launcher=None):
    """Check a saved configuration without launching anything. -> {"ok", "checks": [...], "preview"}."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    store = LauncherStore(config, db)
    record = store.get(game_id)
    if record is None:
        add("launcher", False, "No launcher is registered; run: arch-games discover {} --register 1".format(game_id))
        return {"ok": False, "checks": checks, "preview": None}
    add("launcher", True, "registered ({})".format(record.get("runner")))
    try:
        cfg = profiles.get(record, profile)
        add("profile", True, cfg["profile"])
        profiles.validate_args(cfg["arguments"])
        profiles.validate_env(cfg["environment"], allow_sensitive=True)
        add("arguments and environment", True, "{} argument(s), {} override(s)".format(len(cfg["arguments"]), len(cfg["environment"])))
    except AppError as exc:
        add("profile", False, str(exc))
        return {"ok": False, "checks": checks, "preview": None}
    st = status(config, db, game_id, pacman)
    add("executable", st["status"] == native.READY, st["detail"] or st["label"])
    preview = None
    if st["status"] == native.READY:
        try:
            plan = (launcher or Launcher(config, db, pacman=pacman)).plan_registered(game_id, profile=profile)
            preview = plan["command_preview"]
            add("launch plan", True, "runtime: {}".format(plan["runtime"]))
            for warning in plan.get("warnings", []):
                add("warning", True, warning)
        except AppError as exc:
            add("launch plan", False, str(exc))
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "preview": preview}


def edit(config, db, game_id, change):
    """Apply a pure profiles.* edit to the saved record and persist it. `change(record) -> new record`."""
    store = LauncherStore(config, db)
    record = store.get(game_id)
    if record is None:
        raise AppError("No launcher is registered for this game; run: arch-games discover {} --register 1".format(game_id))
    return store.write(game_id, change(record))
