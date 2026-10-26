"""Wine runtime discovery/abstraction, diagnostics, per-game settings and prefix inspection helpers.

Nothing here downloads Wine or modifies a prefix except functions that say so explicitly."""

from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess

from ..compatibility.graphics import command, detect_graphics
from ..config import AppError
from ..platform import architecture
from .environment import environment

SETTINGS = "game_settings"
WINDOWS_VERSIONS = {"winxp", "win2003", "vista", "win7", "win8", "win81", "win10", "win11"}
DEBUG_CHANNELS = re.compile(r"^[-+A-Za-z0-9_,]{1,200}$")
DLL_MODES = {"n", "b", "n,b", "b,n", "d", ""}
DLL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ENV_ALLOWED_PREFIXES = ("WINE", "DXVK", "VKD3D", "MESA", "LIBGL", "SDL_", "PULSE", "__GL", "LC_", "LANG", "DISPLAY")


@dataclass
class WineRuntime:
    name: str
    wine: str = ""
    wineserver: str = ""
    wineboot: str = ""
    version: str = ""
    layout: str = "unknown"  # wow64 | multilib | win64-only | win32-only | unknown
    evidence: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def classify_layout(wine_path):
    """Derive the build layout from library directories next to the binary. Evidence only; nothing runs."""
    root = Path(wine_path).resolve().parent.parent
    found = {}
    for lib in ("lib", "lib64", "lib32"):
        base = root / lib / "wine"
        for sub in ("i386-unix", "x86_64-unix", "i386-windows", "x86_64-windows"):
            if (base / sub).is_dir():
                found[sub] = True
    ev = ["library dirs under {}: {}".format(root, ", ".join(sorted(found)) or "none found")]
    unix32, unix64 = found.get("i386-unix"), found.get("x86_64-unix")
    win32pe = found.get("i386-windows")
    if unix64 and win32pe and not unix32:
        return "wow64", ev
    if unix64 and unix32:
        return "multilib", ev
    if unix64:
        return "win64-only", ev
    if unix32:
        return "win32-only", ev
    return "unknown", ev


def build_runtime(name, wine_path, run=subprocess.run, which=shutil.which):
    wine = str(wine_path)
    base = Path(wine).parent
    runtime = WineRuntime(name=name, wine=wine)
    for attr, tool in (("wineserver", "wineserver"), ("wineboot", "wineboot")):
        candidate = base / tool
        setattr(runtime, attr, str(candidate) if candidate.is_file() else (which(tool) or ""))
    runtime.version = command([wine, "--version"], run).strip()
    runtime.layout, runtime.evidence = classify_layout(wine) if Path(wine).exists() else ("unknown", ["binary not found"])
    return runtime


def runtimes(config, run=subprocess.run, which=shutil.which):
    """All known runtimes: the system wine plus user-registered paths in settings['wine_runtimes']."""
    result = {}
    system = which("wine")
    if system:
        result["system"] = build_runtime("system", system, run, which)
    for name, path in (config.values.get("wine_runtimes") or {}).items():
        if Path(path).is_file() and os.access(path, os.X_OK):
            result[name] = build_runtime(name, path, run, which)
    return result


def register_runtime(config, name, path):
    path = Path(path).expanduser()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", name) or name == "system":
        raise AppError("Runtime name must be 1..40 safe characters and not 'system'")
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise AppError("Runtime must be an absolute path to an executable wine binary")
    config.values.setdefault("wine_runtimes", {})[name] = str(path)
    config.save()


def prefix_supports(layout, prefix_arch, host=None):
    """Whether this build layout can use a prefix of the given Windows architecture."""
    host = host or architecture()
    if prefix_arch == "win64":
        return layout in {"wow64", "multilib", "win64-only"} and host == "x86_64", "needs a 64-bit host and a 64-bit capable Wine build"
    if prefix_arch == "win32":
        return layout in {"multilib", "win32-only"}, (
            "classic win32 prefixes need a build with 32-bit unix libraries; pure WoW64 builds only create win64 prefixes"
        )
    return False, "unknown prefix architecture"


# -- settings ------------------------------------------------------------------------------------

def get_settings(db, game_id):
    return db.get(SETTINGS, game_id, {}) or {}


def validate_settings(values):
    clean = {}
    if values.get("runtime") is not None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", values["runtime"]):
            raise AppError("Invalid runtime name")
        clean["runtime"] = values["runtime"]
    if values.get("windows_version") is not None:
        if values["windows_version"] not in WINDOWS_VERSIONS:
            raise AppError("Windows version must be one of: " + ", ".join(sorted(WINDOWS_VERSIONS)))
        clean["windows_version"] = values["windows_version"]
    if "dll_overrides" in values:
        overrides = {}
        for dll, mode in (values["dll_overrides"] or {}).items():
            if not DLL_NAME.match(dll) or mode not in DLL_MODES:
                raise AppError("DLL override must be NAME=n|b|n,b|b,n|d")
            overrides[dll] = mode
        clean["dll_overrides"] = overrides
    if "env" in values:
        env = environment(values["env"] or {})
        clean["env"] = {k: v for k, v in (values["env"] or {}).items() if k in env}
        for key in clean["env"]:
            if not key.startswith(ENV_ALLOWED_PREFIXES):
                raise AppError("Environment variable {} is not allowed in per-game Wine settings".format(key))
    if "virtual_desktop" in values:
        vd = values["virtual_desktop"]
        if vd and not re.fullmatch(r"\d{3,5}x\d{3,5}", vd):
            raise AppError("Virtual desktop must look like 1280x720")
        clean["virtual_desktop"] = vd or None
    if "debug" in values:
        dbg = values["debug"]
        if dbg and not DEBUG_CHANNELS.match(dbg):
            raise AppError("Invalid WINEDEBUG channel list")
        clean["debug"] = dbg or None
    return clean


def save_settings(db, game_id, updates):
    current = get_settings(db, game_id)
    current.update(validate_settings(updates))
    db.put(SETTINGS, game_id, current)
    return current


def clear_settings(db, game_id):
    db.delete(SETTINGS, game_id)


def apply_to_plan(settings, plan, runtimes_map):
    """Merge per-game Wine settings into a launcher plan (mutates and returns it)."""
    if plan["method"] != "wine" or not settings:
        return plan
    env = plan["env"]
    if settings.get("dll_overrides"):
        env["WINEDLLOVERRIDES"] = ";".join("{}={}".format(d, m) for d, m in sorted(settings["dll_overrides"].items()))
    if settings.get("debug"):
        env["WINEDEBUG"] = settings["debug"]
    for key, value in (settings.get("env") or {}).items():
        env[key] = value
    if settings.get("runtime"):
        runtime = runtimes_map.get(settings["runtime"])
        if not runtime:
            raise AppError("Configured Wine runtime '{}' is not available".format(settings["runtime"]))
        plan["argv"][0] = runtime.wine
        plan["runtime"] = runtime.name
    if settings.get("virtual_desktop"):
        exe = plan["argv"][1:]
        plan["argv"] = [plan["argv"][0], "explorer", "/desktop=arch-games," + settings["virtual_desktop"]] + exe
    plan["wine_settings"] = {k: v for k, v in settings.items() if v}
    return plan


# -- diagnostics -----------------------------------------------------------------------------------

def check_item(name, status, detail, fix=None):
    return {"check": name, "status": status, "detail": detail, "fix": fix}


def doctor(config, db, run=subprocess.run, which=shutil.which, environ=None, graphics=True):
    environ = os.environ if environ is None else environ
    host = architecture()
    items = []
    found = runtimes(config, run, which)
    if not found:
        items.append(check_item("wine", "fail", "No Wine binary found", "Install wine with pacman (explicitly): sudo pacman -S wine"))
    for name, runtime in found.items():
        items.append(check_item("runtime:" + name, "ok" if runtime.version else "warn", "{} layout={} ({})".format(runtime.version or "version unreadable", runtime.layout, runtime.wine)))
        for tool in ("wineserver", "wineboot"):
            if not getattr(runtime, tool):
                items.append(check_item(tool + ":" + name, "fail", tool + " missing", "Reinstall the wine package"))
        ok64, why64 = prefix_supports(runtime.layout, "win64", host)
        ok32, why32 = prefix_supports(runtime.layout, "win32", host)
        items.append(check_item("prefix-architectures:" + name, "ok" if (ok64 or ok32) else "fail", "win64: {}, win32: {} on {} host".format("yes" if ok64 else "no", "yes" if ok32 else "no", host)))
        if host == "i686" and runtime.layout in {"wow64", "win64-only"}:
            items.append(check_item("host-compat:" + name, "fail", "64-bit Wine build on a 32-bit system", "Use the i686 Wine package"))
    display = bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    items.append(check_item("display", "ok" if display else "warn", "graphical session present" if display else "no DISPLAY/WAYLAND_DISPLAY: Windows games need X11 or Wayland", "Start i3/X or run through your VNC desktop"))
    if display and graphics:  # glxinfo/vulkaninfo/lspci take ~0.5 s: skipped by quick checks
        info = detect_graphics(run, environ)
        renderer = info.get("renderer", "unknown")
        items.append(check_item("graphics", "ok" if info.get("opengl") else "warn", "renderer: {}{}".format(renderer, " (software rendering)" if info.get("accelerated") is False else ""), "Install mesa-utils to measure OpenGL" if info.get("opengl") is None else None))
        if info.get("vulkan") is False or info.get("vulkan") is None:
            items.append(check_item("vulkan", "warn", "Vulkan not available or not measured (DXVK/VKD3D-based games need it)", "Install vulkan-tools / a Vulkan driver (lavapipe works in VMs but is slow)"))
    try:  # one-line audio summary; `audio doctor` has the layers
        from ..audio.diagnose import HEALTHY, diagnose_for

        verdict = diagnose_for(config, db)["verdict"]
        items.append(check_item("audio", "ok" if verdict in HEALTHY else "warn", "audio verdict: " + verdict, None if verdict in HEALTHY else "Run: arch-games audio doctor"))
    except Exception:
        items.append(check_item("audio", "warn", "audio could not be diagnosed", "Run: arch-games audio doctor"))
    prefix_issues = 0
    from .prefixes import PrefixManager

    manager = PrefixManager(config, db)
    for row in db.all("prefixes"):
        try:
            health = manager.check(row["key"])
        except AppError as exc:
            items.append(check_item("prefix:" + row["key"], "fail", str(exc)))
            prefix_issues += 1
            continue
        status = "ok" if health["healthy"] and health["record"].get("state") == "ready" else "warn" if health["healthy"] else "fail"
        prefix_issues += status != "ok"
        items.append(check_item("prefix:" + row["key"], status, "{} ({})".format(health["architecture"], health["record"].get("state")), health["next_action"] if status != "ok" else None))
    return {"host_architecture": host, "checks": items, "summary": {s: sum(1 for i in items if i["status"] == s) for s in ("ok", "warn", "fail")}}


def missing_dependencies(prefix_path, exe_path, imports):
    """Imported DLLs found neither beside the game nor in the prefix's system directories (heuristic)."""
    roots = [Path(exe_path).parent]
    for sub in ("windows/system32", "windows/syswow64"):
        roots.append(Path(prefix_path) / "drive_c" / sub)
    have = set()
    for root in roots:
        try:
            have.update(name.lower() for name in os.listdir(root))
        except OSError:
            pass
    return sorted(d for d in imports if d.lower() not in have)


def status(config, db, game_id, run=subprocess.run, which=shutil.which):
    from .launchers import LauncherStore
    from .prefixes import PrefixManager
    from ..compatibility.executables import inspect_file

    launcher = LauncherStore(config, db).get(game_id)
    settings = get_settings(db, game_id)
    result = {"game_id": game_id, "launcher": launcher, "settings": settings, "problems": [], "suggestions": []}
    prefix_id = (launcher or {}).get("prefix") or game_id
    if not db.get("prefixes", prefix_id):
        result["prefix"] = None
        result["problems"].append("no Wine prefix")
        result["suggestions"].append("arch-games prefix create {}".format(game_id))
        return result
    manager = PrefixManager(config, db, run=run, which=which)
    health = manager.check(prefix_id)
    result["prefix"] = {k: health[k] for k in ("path", "healthy", "architecture", "issues", "next_action")}
    result["prefix"]["state"] = health["record"].get("state")
    if not health["healthy"]:
        result["problems"].append("prefix unhealthy: " + "; ".join(health["issues"]))
    elif health["next_action"]:
        result["problems"].append("prefix state is not ready")
        result["suggestions"].append("arch-games " + health["next_action"])
    found = runtimes(config, run, which)
    chosen = found.get(settings.get("runtime", "system"))
    result["runtime"] = chosen.to_dict() if chosen else None
    if not chosen:
        result["problems"].append("selected Wine runtime unavailable")
    if launcher:
        exe = Path(launcher["executable"])
        if not exe.is_file():
            result["problems"].append("registered executable missing")
        else:
            binary = inspect_file(exe)
            missing = missing_dependencies(health["path"], exe, binary.imports)
            result["missing_dlls_heuristic"] = missing
            if missing:
                result["suggestions"].append("These imported DLLs were not found beside the game or in the prefix: " + ", ".join(missing[:10]) + " (many are Wine built-ins; treat as hints)")
            if binary.bits == 64 and health["architecture"] == "win32":
                result["problems"].append("PE32+ executable cannot run in a win32 prefix")
    launches = sorted((v for v in db.mapping("launches").values() if v.get("game_id") == game_id), key=lambda v: v.get("started_at", ""))
    if launches:
        last = launches[-1]
        result["last_launch"] = {k: last.get(k) for k in ("started_at", "state", "exit_code", "log", "purpose")}
        if last.get("exit_code"):
            result["problems"].append("last launch exited with status {}".format(last["exit_code"]))
            result["suggestions"].append("arch-games wine logs {}".format(game_id))
    return result


def inspect_prefix(config, db, game_id):
    """Size, drives, installed program folders and registry architecture; read-only."""
    from .prefixes import PrefixManager

    health = PrefixManager(config, db).check(game_id)
    path = Path(health["path"])
    size = files = 0
    for root, dirs, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
                files += 1
            except OSError:
                pass
    programs = []
    for sub in ("Program Files", "Program Files (x86)", "GOG Games", "Games"):
        folder = path / "drive_c" / sub
        if folder.is_dir():
            programs += [sub + "/" + e.name for e in sorted(os.scandir(folder), key=lambda e: e.name) if e.is_dir()]
    drives = sorted(e.name for e in os.scandir(path / "dosdevices")) if (path / "dosdevices").is_dir() else []
    return {"game_id": game_id, "path": str(path), "healthy": health["healthy"], "architecture": health["architecture"], "state": health["record"].get("state"), "bytes": size, "files": files, "installed_folders": programs, "drive_mappings": drives, "wine_version": health["record"].get("wine_version"), "issues": health["issues"]}
