"""Small inherited environment; runtime configuration is explicitly local."""

import os
import re
from ..config import AppError

BASE = {
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "LANG",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "DBUS_SESSION_BUS_ADDRESS",
    "PULSE_SERVER",
    "PIPEWIRE_REMOTE",
}


def environment(custom=None):
    result = {k: v for k, v in os.environ.items() if k in BASE or k.startswith("LC_")}
    result.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    for key, value in (custom or {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in {"WINEPREFIX", "WINEARCH"}:
            raise AppError("Invalid or reserved runtime environment variable")
        if not isinstance(value, str) or "\0" in value or len(value) > 8192:
            raise AppError("Invalid runtime environment value")
        result[key] = value
    return result
