"""Redaction of private paths, identities, network addresses and credentials in shareable diagnostics."""

import getpass
import os
from pathlib import Path
import re

SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|token|authorization)\s*[:=]?\s*[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    (re.compile(r"(?i)\b(password|passwd|api[_-]?key|secret|access_token|refresh_token)\b(\s*[:=]\s*)\S+"), r"\1\2<redacted>"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
]
IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
TAILNET = re.compile(r"\b[\w-]+\.[\w-]+\.ts\.net\b")


def redact(text, extra_paths=(), secrets=(), keep_loopback=True):
    """Return text safe to share. extra_paths: [(path, label)] replaced longest-first."""
    text = str(text)
    for secret in secrets:
        if secret and len(str(secret)) >= 4:
            text = text.replace(str(secret), "<redacted>")
    replacements = [(str(p), label) for p, label in extra_paths if p]
    home = str(Path.home())
    replacements.append((home, "~"))
    for path, label in sorted(replacements, key=lambda x: -len(x[0])):
        if len(path) > 1:
            text = text.replace(path, label)
    try:
        user = getpass.getuser()
    except Exception:  # no passwd entry in minimal containers
        user = os.environ.get("USER", "")
    if len(user) >= 3:
        text = re.sub(r"(?<![\w-])" + re.escape(user) + r"(?![\w-])", "<user>", text)
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)

    def ip(match):
        value = match.group(0)
        if keep_loopback and (value.startswith("127.") or value in {"0.0.0.0"}):
            return value
        return "<ip>"

    text = IPV4.sub(ip, text)
    text = TAILNET.sub("<tailnet-host>", text)
    return text
