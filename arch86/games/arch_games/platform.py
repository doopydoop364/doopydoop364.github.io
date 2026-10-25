"""Platform detection with explicit unknown states."""
import platform as host_platform


def architecture(value=None):
    value = (value or host_platform.machine()).lower()
    if value in {"i386", "i486", "i586", "i686", "pentium4", "x86"}:
        return "i686"
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    return value or "unknown"
