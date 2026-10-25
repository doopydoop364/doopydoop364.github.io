"""Platform detection with explicit unknown states."""
import platform as host_platform


def architecture(value=None):
    value = (value or host_platform.machine()).lower()
    if value in {"i386", "i486", "i586", "i686", "pentium4", "x86"}:
        return "i686"
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    return value or "unknown"


def system_profile(path=None, run=None, which=None, environ=None):
    """Measure host resources; kernel architecture differs from Python process width."""
    import os
    from pathlib import Path
    import shutil
    import struct
    import subprocess
    from .compatibility.graphics import detect_graphics
    from .compatibility.wine import detect_wine
    memory = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    target = Path(path or Path.home())
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        free = shutil.disk_usage(target).free
    except OSError:
        free = None
    runner = run or subprocess.run
    finder = which or shutil.which
    return {"cpu_architecture": architecture(), "os_architecture": architecture(),
            "process_bits": struct.calcsize("P") * 8, "os": host_platform.system().lower(),
            "ram_total": memory.get("MemTotal"), "ram_available": memory.get("MemAvailable"), "disk_free": free,
            "cpus": os.cpu_count(), "graphics": detect_graphics(runner, environ),
            "wine": detect_wine(runner, finder),
            "runtimes": {name: finder(name) for name in ["dosbox", "dosbox-staging", "proton", "steam"]},
            "evidence": ["Kernel architecture (CPU execution mode); /proc/meminfo; filesystem free space"],
            "uncertainties": ["CPU hardware may support more modes than the running kernel; no execution probe performed"]}


def apply_overrides(profile, config):
    for capability in ["win32", "win64", "wow64"]:
        key = "wine_" + capability
        if key in config.values:
            profile.setdefault("wine", {})[capability] = config.values[key]
            message = "User-asserted capability: " + capability
            evidence = profile["wine"].setdefault("evidence", [])
            if message not in evidence:
                evidence.append(message)
    return profile
