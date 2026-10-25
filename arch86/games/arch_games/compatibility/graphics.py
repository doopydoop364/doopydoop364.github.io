"""Conservative graphics measurements. Unknown does not mean available."""
import os
from pathlib import Path
import subprocess


def command(args, run=subprocess.run):
    try:
        result = run(args, capture_output=True, text=True, timeout=5)
        return result.stdout[:65536] if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def detect_graphics(run=subprocess.run, environ=None):
    environ = os.environ if environ is None else environ
    display = bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))
    adapters = [line.strip() for line in command(["lspci", "-nn"], run).splitlines()
                if any(word in line.lower() for word in ["vga", "3d controller", "display controller"])]
    gl = command(["glxinfo", "-B"], run) if display else ""
    vk = command(["vulkaninfo", "--summary"], run)
    lower = (gl + "\n" + vk).lower()
    software = any(s in lower for s in ["llvmpipe", "softpipe", "lavapipe", "software rasterizer", "cpu)"])
    accelerated = False if software else None
    if not software and "accelerated: yes" in gl.lower():
        accelerated = True
    vulkan = bool(vk and "devicename" in vk.lower())
    return {"display": display, "adapters": adapters, "opengl": bool(gl) if display else None,
            "vulkan": vulkan if vk else None, "accelerated": accelerated,
            "renderer": next((line.strip() for line in gl.splitlines() if "renderer string" in line.lower()), "unknown"),
            "evidence": ["glxinfo -B" if gl else "OpenGL not measured", "vulkaninfo --summary" if vk else "Vulkan not measured"],
            "uncertainties": ["Virtual adapter performance requires actual gameplay testing"]}
