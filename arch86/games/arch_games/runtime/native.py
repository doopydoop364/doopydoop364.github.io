"""Native Linux games installed through Pacman: package-aware executable discovery, status and launcher verification.

Trust comes from package ownership (`pacman -Ql` / `-Qo`) plus filesystem evidence under trusted roots; /usr/bin is never scanned
blindly and nothing is executed to identify it. Desktop entries are parsed, never run through a shell."""

import os
from pathlib import Path

from ..compatibility.executables import inspect_file
from ..config import AppError
from ..identity.resolver import normalize
from .discovery import NON_GAME_ROLES, classify_name
from .pacman import parse_desktop_entry, parse_exec, read_text

from .pacman import PACKAGE as PACKAGE_RE  # noqa: E402

# Module-level so tests can point them at a temporary tree; production values are the real system directories.
TRUSTED_ROOTS = ("/usr", "/opt")
STD_BIN_DIRS = ("/usr/bin/", "/usr/games/")
LIB_DIRS = ("/usr/lib/", "/usr/lib32/", "/opt/")
MAX_INSPECTED = 250

# status values shared by the CLI, TUI and web GUI
AVAILABLE = "available"
NO_LAUNCHER = "installed-no-launcher"
READY = "ready"
INVALID = "launcher-invalid"
REMOVED = "package-removed"
LABELS = {
    AVAILABLE: "Available in the repositories, not installed",
    NO_LAUNCHER: "Installed through Pacman; no launcher registered yet",
    READY: "Installed and ready to launch",
    INVALID: "Launcher is missing or no longer belongs to the package",
    REMOVED: "Package is no longer installed",
}


def package_of(game_id, game=None):
    """The Pacman package behind a game, or None for non-Pacman games."""
    if game is not None and getattr(game, "package", ""):
        return game.package
    return game_id.split(":", 1)[1] if game_id.startswith("pacman:") else None


def _under(path, roots):
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def shebang(path):
    try:
        with open(path, "rb") as stream:
            head = stream.read(256)
    except OSError:
        return None
    if head.startswith(b"#!"):
        line = head.split(b"\n", 1)[0][2:].decode("utf-8", "replace").strip()
        return line or None
    return None


def desktop_launchers(files, read=read_text):
    """{owned executable path: {"name","args","categories","hidden"}} from the package's own .desktop files."""
    targets = {}
    for path in files:
        if not (path.endswith(".desktop") and "/share/applications/" in path):
            continue
        entry = parse_desktop_entry(read(path))
        if entry.get("Type", "Application") != "Application" or not entry.get("Exec"):
            continue
        program, args, env = parse_exec(entry["Exec"])
        if not program:
            continue
        targets.setdefault(program, []).append({
            "name": entry.get("Name", Path(path).stem), "args": args, "env": env,
            "games": "Game" in entry.get("Categories", ""), "hidden": entry.get("NoDisplay", "").lower() == "true",
            "desktop": path,
        })
    return targets


def resolve_target(program, fileset, bin_dirs=None):
    """Map a desktop-entry program onto a path the package owns: absolute paths must be owned; bare names are looked up only
    among the package's own bin-directory files (PATH is never searched)."""
    if program.startswith("/"):
        return program if program in fileset else None
    bin_dirs = bin_dirs or STD_BIN_DIRS
    for path in sorted(fileset):
        if os.path.basename(path) == program and any(path.startswith(d) and "/" not in path[len(d):] for d in bin_dirs):
            return path
    return None


def discover_native(pacman, package, title="", include_excluded=False, roots=None, read=read_text):
    """-> {"installed", "version", "package", "candidates", "confidence"}. Reads the package file list and file headers only."""
    roots = roots or TRUSTED_ROOTS
    version = pacman.version(package)
    result = {"installed": version is not None, "version": version, "package": package, "candidates": [], "confidence": "none"}
    if version is None:
        return result
    files = pacman.files(package)
    fileset = set(files)
    desktop = desktop_launchers(files, read)
    desktop_by_path = {}
    for program, entries in desktop.items():
        owned = resolve_target(program, fileset)
        if owned:
            desktop_by_path.setdefault(owned, []).extend(entries)
    key_title, key_pkg = normalize(title), normalize(package)
    scanned, found = 0, []
    for path in files:
        if not _under(path, roots):
            continue
        in_bin = any(path.startswith(d) and "/" not in path[len(d):] for d in STD_BIN_DIRS)
        in_lib = any(path.startswith(d) and path[len(d):].count("/") <= 2 for d in LIB_DIRS)
        if not (in_bin or in_lib or path in desktop_by_path):
            continue
        name = os.path.basename(path)
        if ".so" in name or name.endswith((".a", ".la", ".py", ".pyc")) and path not in desktop_by_path:
            continue
        scanned += 1
        if scanned > MAX_INSPECTED:
            break
        p = Path(path)
        try:
            resolved = p.resolve() if p.is_symlink() else p
            owned = str(resolved) in fileset or pacman.owner(resolved) == package  # a symlink must point at a file the package owns
            if p.is_symlink() and not owned:
                continue
            st = resolved.stat()
        except OSError:
            continue
        if not resolved.is_file() or not st.st_mode & 0o111:
            continue
        info = inspect_file(resolved).to_dict()
        kind = "elf" if info.get("os") == "linux" else None
        interpreter = None
        if kind is None:
            interpreter = shebang(resolved)
            if interpreter is None:
                continue
            kind = "script"
        role = classify_name(name)
        evidence = ["Owned by package {} {}".format(package, version)]
        if p.is_symlink():
            evidence.append("Symlink to {}, which the package owns".format(resolved))
        evidence.append("Linux ELF {} {}".format(info.get("format"), info.get("architecture")) if kind == "elf" else "Wrapper script ({})".format(interpreter))
        score = 20
        label = normalize(name)
        if role in NON_GAME_ROLES or role == "system":
            found.append({**_candidate(path, resolved, info, kind, name, role, -100, [evidence[0], "Classified as {} by file name".format(role.replace("_", " "))], package)})
            continue
        if label == key_pkg:
            score += 40
            evidence.append("Executable name matches the package name")
        elif key_pkg and (key_pkg in label or label in key_pkg) and len(label) >= 3:
            score += 20
            evidence.append("Executable name overlaps the package name")
        if key_title and label and (label == key_title.replace(" ", "") or label in key_title.replace(" ", "")):
            score += 15
            evidence.append("Executable name matches the game title")
        if in_bin:
            score += 10
            evidence.append("Installed in a standard program directory")
        elif in_lib:
            score -= 5
            evidence.append("Lives in the package's private library directory (often a helper)")
        launch = desktop_by_path.get(path) or desktop_by_path.get(str(resolved))
        extra = {}
        if launch:
            visible = [d for d in launch if not d["hidden"]] or launch
            score += 40 + (10 if any(d["games"] for d in launch) else 0)
            evidence.append("Desktop entry '{}' launches it".format(visible[0]["name"]))
            extra = {"desktop": {"name": visible[0]["name"], "arguments": visible[0]["args"], "file": visible[0]["desktop"]}}
        found.append({**_candidate(path, resolved, info, kind, name, role, score, evidence, package), **extra})
    viable = [c for c in found if c["role"] == "game"]
    if len(viable) == 1:
        viable[0]["score"] += 25
        viable[0]["evidence"].append("The only launchable program in the package")
    found.sort(key=lambda c: (-c["score"], c["path"]))
    if not include_excluded:
        found = [c for c in found if c["role"] == "game"]
    for rank, c in enumerate(found, 1):
        c["rank"] = rank
    result["candidates"] = found
    result["confidence"] = native_confidence(found)
    return result


def native_confidence(candidates):
    """'high' only when the choice is not a guess: a single launchable program, or exactly one that a desktop entry launches
    and whose name matches the package. Anything else asks the user to confirm."""
    viable = [c for c in candidates if c["role"] == "game"]
    if not viable:
        return "none"
    if len(viable) == 1:
        return "high"
    with_entry = [c for c in viable if c.get("desktop")]
    if len(with_entry) == 1 and any("matches the package name" in e for e in with_entry[0]["evidence"]):
        return "high"
    return "uncertain"


def _candidate(path, resolved, info, kind, name, role, score, evidence, package):
    return {
        "path": path, "resolved": str(resolved), "name": name, "role": role, "score": score, "evidence": evidence,
        "origin": "Pacman package " + package, "format": info.get("format") if kind == "elf" else "script",
        "architecture": info.get("architecture") if kind == "elf" else "unknown", "os": "linux", "bits": info.get("bits"), "kind": kind,
    }


def verify_launcher(pacman, record, roots=None):
    """Is this registered native launcher still valid? -> (status, detail). One `pacman -Q` and at most one `-Qo`."""
    roots = roots or TRUSTED_ROOTS
    package = record.get("package")
    path = record.get("executable", "")
    if not package:
        return INVALID, "Launcher record has no package"
    version = pacman.version(package)
    if version is None:
        return REMOVED, "Package {} is not installed".format(package)
    if not _under(path, roots):
        return INVALID, "Executable is outside the trusted program directories"
    p = Path(path)
    if not p.exists():
        return INVALID, "{} no longer exists (package {} is now {}); rediscover it".format(path, package, version)
    resolved = p.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return INVALID, "{} is not an executable file".format(path)
    owner = pacman.owner(path if not p.is_symlink() else resolved)
    if owner != package:
        return INVALID, "{} is owned by {} rather than {}".format(path, owner or "no package", package)
    return READY, "Package {} {} owns {}".format(package, version, path)


def status(game_installed, package_version, record, file_exists):
    """Cheap, subprocess-free status for lists (the package version map comes from one `pacman -Q`)."""
    if record is not None and record.get("kind") == "native-package":
        if not game_installed:
            return REMOVED
        return READY if file_exists else INVALID
    if game_installed:
        return NO_LAUNCHER if record is None else READY
    return AVAILABLE


def require_native_candidate(candidates, rank):
    chosen = next((c for c in candidates if c["rank"] == rank), None)
    if not chosen:
        raise AppError("No candidate with that rank")
    return chosen


def verify_candidate(pacman, package, path, roots=None):
    """Re-verify, right before registering or launching, that `path` is an executable owned by the installed package.
    Returns (link_path, resolved_path); raises AppError with the reason otherwise."""
    roots = roots or TRUSTED_ROOTS
    if not PACKAGE_RE.match(package or ""):
        raise AppError("Invalid package name")
    if pacman.version(package) is None:
        raise AppError("Package {} is not installed".format(package))
    path = str(path)
    if not _under(path, roots):
        raise AppError("Refusing {}: native launchers must live under /usr or /opt".format(path))
    link = Path(path)
    if not link.exists():
        raise AppError("{} does not exist".format(path))
    resolved = link.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise AppError("{} is not an executable file".format(path))
    if pacman.owner(resolved if link.is_symlink() else link) != package:
        raise AppError("{} is not owned by package {}; refusing to use it".format(path, package))
    return link, resolved
