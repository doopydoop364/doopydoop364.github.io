"""Conservative graphics measurements. Unknown does not mean available.

Probes (`lspci`, `glxinfo`, `vulkaninfo`) are external programs and some crash: `vulkaninfo` segfaults when DISPLAY is set but the
X connection fails. So: vulkaninfo never sees DISPLAY/WAYLAND_DISPLAY (we only read device names), glxinfo runs only when a display
is really reachable, a probe killed by a signal is recorded as 'crashed' and not retried for a day, and the whole result is cached
per process and on disk for ten minutes. With a real runner `ARCH_GAMES_NO_PROBE=1` disables probing entirely (the test suite sets it)."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

TTL = 600.0  # seconds a measurement is reused (per process and on disk)
CRASH_TTL = 24 * 3600.0  # seconds a crashing tool is left alone
DISPLAY_VARS = ("DISPLAY", "WAYLAND_DISPLAY")

_lock = threading.Lock()
_memory = {}


def command(args, run=subprocess.run):
    try:
        result = run(args, capture_output=True, text=True, timeout=5)
        return result.stdout[:65536] if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def probe(args, run, env=None):
    """-> (stdout, status). status: ok | failed | missing | timeout | crashed (killed by a signal, e.g. -11 or 139)."""
    kwargs = {"capture_output": True, "text": True, "timeout": 5}
    if env is not None:
        kwargs["env"] = env
    try:
        result = run(args, **kwargs)
    except FileNotFoundError:
        return "", "missing"
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except OSError:
        return "", "failed"
    code = result.returncode
    if code < 0 or code >= 128:
        return "", "crashed"
    return (result.stdout or "")[:65536], "ok" if code == 0 else "failed"


def display_reachable(environ):
    """Is there a display a client could plausibly connect to? (Only checks sockets and the Xauthority file; connects to nothing.)"""
    display, wayland = environ.get("DISPLAY", ""), environ.get("WAYLAND_DISPLAY", "")
    xauth = environ.get("XAUTHORITY")
    if xauth and not os.access(xauth, os.R_OK):
        return False
    if display.startswith(":"):
        number = display[1:].split(".", 1)[0]
        if number.isdigit() and os.path.exists("/tmp/.X11-unix/X" + number):
            return True
    runtime = environ.get("XDG_RUNTIME_DIR")
    if wayland and runtime and os.path.exists(os.path.join(runtime, wayland)):
        return True
    return False


def cache_file():
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "arch-games" / "graphics.json"


def read_disk():
    try:
        path = cache_file()
        if path.is_symlink() or path.stat().st_size > 256 * 1024:
            return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_disk(data):
    try:
        path = cache_file()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".graphics-")
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
        os.chmod(name, 0o600)
        os.replace(name, path)
    except OSError:
        pass  # the cache is an optimisation only


def clear_cache():
    """Forget measurements (an explicit refresh): the next detect_graphics probes again."""
    with _lock:
        _memory.clear()
    try:
        cache_file().unlink()
    except OSError:
        pass


def detect_graphics(run=None, environ=None):
    run = run or subprocess.run
    real = run is subprocess.run  # the default runner is the real one; injected fakes (tests) are measured directly
    environ = os.environ if environ is None else environ
    if not real:  # never cached
        return _measure(run, environ, {})
    key = [environ.get("DISPLAY"), environ.get("WAYLAND_DISPLAY")]
    now = time.time()
    with _lock:
        hit = _memory.get("result")
        if hit and hit["key"] == key and now - hit["at"] < TTL:
            return json.loads(json.dumps(hit["result"]))
        if os.environ.get("ARCH_GAMES_NO_PROBE"):
            return _unmeasured(environ, "Graphics probing is disabled (ARCH_GAMES_NO_PROBE)")
        disk = read_disk()
        stored = disk.get("result")
        if stored and disk.get("key") == key and now - disk.get("at", 0) < TTL:
            _memory["result"] = {"key": key, "at": disk["at"], "result": stored}
            return json.loads(json.dumps(stored))
        crashes = {tool: at for tool, at in (disk.get("crashes") or {}).items() if now - at < CRASH_TTL}
        result = _measure(run, environ, crashes)
        for tool, status in result.pop("_statuses").items():
            if status == "crashed" and tool not in crashes:  # keep the original time: the window must expire
                crashes[tool] = now
        _memory["result"] = {"key": key, "at": now, "result": result}
        write_disk({"at": now, "key": key, "result": result, "crashes": crashes})
        return json.loads(json.dumps(result))


def _unmeasured(environ, why):
    return {
        "display": bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")), "adapters": [], "opengl": None, "vulkan": None,
        "accelerated": None, "renderer": "unknown", "evidence": [why],
        "uncertainties": ["Virtual adapter performance requires actual gameplay testing"],
    }


def _measure(run, environ, crashed_recently):
    real = run is subprocess.run
    display = bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    statuses, notes = {}, []

    def tool(name, args, env=None, skip_crashed=True):
        if skip_crashed and name in crashed_recently:
            statuses[name] = "crashed"
            return ""
        out, status = probe(args, run, env)
        statuses[name] = status
        return out

    adapters = [
        line.strip()
        for line in tool("lspci", ["lspci", "-nn"]).splitlines()
        if any(word in line.lower() for word in ["vga", "3d controller", "display controller"])
    ]
    gl = ""
    if display and (not real or display_reachable(environ)):
        gl = tool("glxinfo", ["glxinfo", "-B"])
    elif display:
        notes.append("glxinfo not run: the display is not reachable from this process (no X socket or unreadable XAUTHORITY)")
    # Only device names are needed, and vulkaninfo segfaults when DISPLAY is set but unusable: it never sees a display.
    clean_env = {k: v for k, v in environ.items() if k not in DISPLAY_VARS}
    vk = tool("vulkaninfo", ["vulkaninfo", "--summary"], clean_env)
    lower = (gl + "\n" + vk).lower()
    software = any(s in lower for s in ["llvmpipe", "softpipe", "lavapipe", "software rasterizer", "cpu)"])
    accelerated = False if software else None
    if not software and "accelerated: yes" in gl.lower():
        accelerated = True
    vulkan = bool(vk and "devicename" in vk.lower())
    uncertainties = ["Virtual adapter performance requires actual gameplay testing"]
    for name in ("glxinfo", "vulkaninfo"):
        if statuses.get(name) == "crashed":
            uncertainties.append("{}: probe crashed (left alone for a day); the result is unknown, not 'unavailable'".format(name.replace("info", "")))
        elif statuses.get(name) == "timeout":
            uncertainties.append("{}: probe timed out".format(name.replace("info", "")))
    result = {
        "display": display,
        "adapters": adapters,
        "opengl": bool(gl) if display else None,
        "vulkan": vulkan if vk else None,
        "accelerated": accelerated,
        "renderer": next((line.strip() for line in gl.splitlines() if "renderer string" in line.lower()), "unknown"),
        "evidence": [
            "glxinfo -B" if gl else "OpenGL not measured",
            "vulkaninfo --summary" if vk else "Vulkan not measured",
        ] + notes,
        "uncertainties": uncertainties,
        "_statuses": statuses,
    }
    if not real:
        result.pop("_statuses")
    return result
