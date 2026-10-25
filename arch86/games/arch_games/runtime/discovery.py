"""Find which executable actually starts a game, without running anything.

Evidence is gathered from file names, bounded PE/ELF headers, GOG ``goggame-*.info`` play tasks,
Windows uninstall registry records, neighbouring asset files and earlier successful launches.
Every candidate carries an explicit score breakdown so the ranking is explainable.
"""

import json
import os
from pathlib import Path
import re

from ..compatibility.executables import inspect_file
from ..config import AppError
from ..identity.resolver import normalize
from .installer import child
from .shortcuts import desktop_entries_for_prefix, parse_lnk, prefix_shortcuts

MAX_ENTRIES = 6000
MAX_DEPTH = 6
MAX_REG_BYTES = 24 * 1024 * 1024
SKIP_DIRS = {"windows", "programdata", "users", "$recycle.bin", "system volume information"}
PRUNE_DIRS = {"__pycache__", ".git", "redist", "_commonredist", "directx", "dotnet", "vcredist"}
SYSTEM_EXES = {"iexplore.exe", "wordpad.exe", "notepad.exe", "explorer.exe", "uninstall.exe"}

# Order matters: the first matching role wins.
ROLE_PATTERNS = [
    ("uninstaller", r"^(unins\d*|uninst\w*|uninstall\w*|remove\w*)$"),
    ("redistributable", r"^(vc_?redist\S*|vcredist\S*|dxsetup|dxwebsetup|dotnet\S*|ndp\S*|oalinst|physx\S*|xnafx\S*|directx\S*|dxsetup\S*)$"),
    ("crash_reporter", r"^(\S*crash\S*|\S*bugreport\S*|\S*errorreport\S*|\S*sentry\S*|werfault)$"),
    ("updater", r"^(\S*updater?|update\S*|patch\w*|autoupdate\S*|\S*selfupdate\S*)$"),
    ("installer", r"^(setup\S*|install\S*|installer\S*|inst\d*)$"),
    ("config_utility", r"^(\S*config\S*|\S*settings?\S*|\S*setup_tool\S*|\S*options\S*|\S*benchmark\S*|\S*editor\S*)$"),
    ("launch_helper", r"^(\S*helper\S*|\S*subprocess\S*|\S*webhelper\S*|\S*server\S*|\S*service\S*|\S*cefprocess\S*)$"),
]
NON_GAME_ROLES = {r for r, _ in ROLE_PATTERNS}
ASSET_SUFFIXES = {".pak", ".dat", ".win", ".wad", ".assets", ".bank", ".ogg", ".wav", ".bsp", ".vpk", ".arc", ".bin", ".xnb", ".pck", ".npk", ".big", ".rpa", ".unity3d", ".resource", ".ress"}


def classify_name(filename):
    stem = re.sub(r"\.(exe|bat|sh|x86_64|x86|bin)$", "", filename.lower())
    if filename.lower() in SYSTEM_EXES:
        return "system"
    for role, pattern in ROLE_PATTERNS:
        if re.match(pattern, stem):
            return role
    return "game"


def win_to_unix(prefix, windows_path):
    """Map ``C:\\Foo\\Bar`` into the prefix's drive_c, matching case-insensitively; None if absent or unsafe."""
    text = str(windows_path).replace("\\\\", "\\")
    match = re.match(r"^[cC]:[\\/]*(.*)$", text)
    if not match:
        return None
    current = Path(prefix) / "drive_c"
    for part in [p for p in re.split(r"[\\/]", match.group(1)) if p]:
        if part in {".", ".."} or "\0" in part:
            return None
        try:
            names = {entry.lower(): entry for entry in os.listdir(current)}
        except OSError:
            return None
        if part.lower() not in names:
            return None
        current = current / names[part.lower()]
        if current.is_symlink():
            return None
    return current


def registry_install_locations(prefix):
    """Uninstall-key records from system.reg: [{"name","location","uninstaller"}]. Bounded line scan."""
    result = []
    for name in ("system.reg", "user.reg"):
        path = Path(prefix) / name
        try:
            if path.is_symlink() or not path.is_file():
                continue
            size = 0
            record = None
            in_uninstall = False
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    size += len(line)
                    if size > MAX_REG_BYTES:
                        break
                    if line.startswith("["):
                        if record and (record.get("location") or record.get("uninstaller")):
                            result.append(record)
                        in_uninstall = "\\\\Uninstall\\\\" in line
                        record = {"name": "", "location": "", "uninstaller": "", "key": line.split("]")[0][1:]} if in_uninstall else None
                    elif record is not None:
                        m = re.match(r'^"(DisplayName|InstallLocation|UninstallString)"="(.*)"\s*$', line)
                        if m:
                            field = {"DisplayName": "name", "InstallLocation": "location", "UninstallString": "uninstaller"}[m.group(1)]
                            record[field] = m.group(2)[:1024]
            if record and (record.get("location") or record.get("uninstaller")):
                result.append(record)
        except OSError:
            continue
    return result


def _location_from_record(prefix, record):
    if record["location"]:
        return win_to_unix(prefix, record["location"])
    uninstaller = record["uninstaller"].strip('" ')
    if uninstaller:
        folder = re.split(r"[\\/][^\\/]*$", uninstaller.split('"')[0])[0]
        return win_to_unix(prefix, folder)
    return None


def gog_primary_tasks(directory):
    """goggame-*.info play tasks -> {lowercased relative path: task}."""
    tasks = {}
    try:
        for info in Path(directory).glob("goggame-*.info"):
            if info.is_symlink() or info.stat().st_size > 1024 * 1024:
                continue
            data = json.loads(info.read_text(encoding="utf-8", errors="replace"))
            for task in data.get("playTasks", []) if isinstance(data, dict) else []:
                if isinstance(task, dict) and task.get("path") and task.get("category", "game") == "game":
                    tasks[str(task["path"]).replace("\\", "/").lower()] = dict(task, game_name=data.get("name"))
    except (OSError, ValueError):
        pass
    return tasks


def _walk(root, max_depth=MAX_DEPTH, limit=MAX_ENTRIES):
    """Yield executable files under root without following symlinks. Bounded by depth and entry count."""
    count = 0
    stack = [(Path(root), 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    count += 1
                    if count > limit:
                        return
                    if entry.is_symlink():
                        continue
                    lowered = entry.name.lower()
                    if entry.is_dir(follow_symlinks=False):
                        if depth < max_depth and lowered not in PRUNE_DIRS and not (depth == 0 and lowered in SKIP_DIRS):
                            stack.append((Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=False) and (
                        lowered.endswith((".exe", ".sh", ".x86_64", ".x86", ".bin"))
                        or (not Path(lowered).suffix and entry.stat(follow_symlinks=False).st_mode & 0o111)
                    ):
                        yield Path(entry.path), depth
        except OSError:
            continue


def search_roots(config, db, game_id, extra=()):
    """(root, origin) pairs worth scanning for this game; all stay inside managed storage unless the user adds one."""
    roots = []
    manifest = db.get("installs", game_id)
    if manifest:
        try:
            roots.append((child(config.directory("installed"), manifest["directory"]), "managed install"))
        except AppError:
            pass
    prefix = prefix_path(config, db, game_id)
    if prefix:
        drive = prefix / "drive_c"
        seen = set()
        for record in registry_install_locations(prefix):
            location = _location_from_record(prefix, record)
            if location and location.is_dir() and location not in seen and location != drive:
                seen.add(location)
                roots.append((location, "Windows uninstall registry record"))
        for name in ("Program Files", "Program Files (x86)", "GOG Games", "Games"):
            folder = drive / name
            if folder.is_dir() and not folder.is_symlink() and folder not in seen:
                roots.append((folder, "prefix " + name))
    for folder in extra:
        folder = Path(folder).expanduser()
        if not folder.is_dir():
            raise AppError("Search directory does not exist: " + str(folder))
        roots.append((folder.resolve(), "user-specified directory"))
    return roots


def prefix_path(config, db, game_id, prefix_id=None):
    record = db.get("prefixes", prefix_id or game_id)
    if not record:
        return None
    try:
        return child(config.directory("prefixes"), record["directory"])
    except AppError:
        return None


def _successful_launch_paths(db, game_id):
    paths = {}
    for launch in db.mapping("launches").values():
        if (
            launch.get("game_id") == game_id
            and launch.get("exit_code") == 0
            and launch.get("executable")
            and launch.get("purpose", "game") == "game"
        ):
            paths[launch["executable"]] = max(paths.get(launch["executable"], ""), launch.get("started_at", ""))
    return paths


def score_candidate(path, depth, origin, title, info, tasks, launches, shortcut=None):
    breakdown = []
    name = path.name
    role = classify_name(name)
    if info.get("role") == "installer" and role == "game":
        role = "installer"
    total = 0
    if role in NON_GAME_ROLES or role == "system":
        return role, -100, ["Classified as {} by file name{}".format(role, "/header" if info.get("role") == "installer" else "")]
    if info.get("os") not in {"windows", "linux", "dos"}:
        return "unknown", -50, ["No recognised executable header"]
    breakdown.append("Valid {} {} header".format(info.get("format"), info.get("architecture")))
    total += 5
    task = tasks
    if task:
        total += 70 if task.get("isPrimary") else 45
        breakdown.append("GOG metadata lists this as the {} play task".format("primary" if task.get("isPrimary") else "a"))
    stem = normalize(path.stem)
    key = normalize(title)
    if key and stem:
        if stem == key or stem.replace(" ", "") == key.replace(" ", ""):
            total += 40
            breakdown.append("File name matches the game title")
        elif stem in key or key in stem:
            total += 22
            breakdown.append("File name overlaps the game title")
    if depth <= 1:
        total += 8
        breakdown.append("Located near the top of the install directory")
    elif depth >= 4:
        total -= 6
        breakdown.append("Deeply nested")
    if origin in {"Windows uninstall registry record", "managed install"}:
        total += 10
        breakdown.append("Inside a directory identified by " + origin)
    siblings = 0
    try:
        with os.scandir(path.parent) as neighbours:
            for sibling in neighbours:
                if Path(sibling.name).suffix.lower() in ASSET_SUFFIXES:
                    siblings += 1
                    if siblings >= 3:
                        break
    except OSError:
        pass
    if siblings:
        total += 10 if siblings >= 3 else 5
        breakdown.append("Game asset files sit alongside it")
    if info.get("subsystem") == "Windows GUI":
        total += 4
        breakdown.append("Windows GUI subsystem")
    if info.get("subsystem") == "Windows console":
        total -= 4
    if shortcut:
        total += 25
        breakdown.append("A {} points to it".format(shortcut))
    previous = launches.get(str(path))
    if previous:
        total += 50
        breakdown.append("Launched successfully before ({})".format(previous))
    try:
        size = path.stat().st_size
        if size > 1_000_000:
            total += 6
            breakdown.append("Large executable (>1 MB)")
        elif size < 100_000:
            total -= 8
    except OSError:
        pass
    return role, total, breakdown


def discover(config, db, game_id, title="", extra_dirs=(), include_excluded=False, prefix_id=None):
    """Ranked candidate list. Reads headers only; nothing is executed."""
    roots = search_roots(config, db, game_id, extra_dirs)
    if prefix_id and prefix_id != game_id:
        pp = prefix_path(config, db, game_id, prefix_id)
        if pp:
            roots.append((pp / "drive_c", "prefix drive_c"))
    launches = _successful_launch_paths(db, game_id)
    shortcut_targets = {}
    pp = prefix_path(config, db, game_id, prefix_id)
    if pp:
        links = dict((p, v) for p, v in prefix_shortcuts(pp).items())
        for entry in desktop_entries_for_prefix(pp):
            if entry["lnk"] and Path(entry["lnk"]).is_file():
                parsed = parse_lnk(entry["lnk"])
                if parsed:
                    links[entry["lnk"]] = dict(parsed, desktop=True)
        for lnk, parsed in links.items():
            target = win_to_unix(pp, parsed["target"])
            if target and target.is_file():
                shortcut_targets[str(target.resolve())] = "desktop entry shortcut" if parsed.get("desktop") else "Start Menu/Desktop shortcut"
    candidates, seen = [], set()
    for root, origin in roots:
        task_cache = {}

        def task_for(path):
            for parent in [path.parent, *path.parent.parents]:
                if parent not in task_cache:
                    task_cache[parent] = gog_primary_tasks(parent)
                found = task_cache[parent].get(path.relative_to(parent).as_posix().lower())
                if found:
                    return found
                if parent == root:
                    break
            return None

        for path, depth in _walk(root):
            real = str(path.resolve())
            if real in seen:
                continue
            seen.add(real)
            info = inspect_file(path).to_dict() if path.suffix.lower() != ".sh" else {"os": "linux", "format": "script", "architecture": "unknown"}
            if info.get("os") == "linux" and info.get("format") == "script":
                continue
            role, score, why = score_candidate(path, depth, origin, title, info, task_for(path), launches, shortcut_targets.get(real))
            if role != "game" and not include_excluded:
                continue
            candidates.append(
                {
                    "path": real,
                    "name": path.name,
                    "role": role,
                    "score": score,
                    "evidence": why,
                    "origin": origin,
                    "format": info.get("format"),
                    "architecture": info.get("architecture"),
                    "os": info.get("os"),
                    "bits": info.get("bits"),
                }
            )
    candidates.sort(key=lambda c: (-c["score"], c["path"]))
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def confidence(candidates):
    games = [c for c in candidates if c["role"] == "game"]
    if not games:
        return "none"
    if (len(games) == 1 and games[0]["score"] >= 50) or (
        games[0]["score"] >= 70 and games[0]["score"] - games[1]["score"] >= 20
    ):
        return "high"
    return "uncertain"
