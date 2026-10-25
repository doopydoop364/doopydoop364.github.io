"""Safe public archive errors and opt-in, bounded, credential-redacted diagnostics."""

import os
import re
import unicodedata

from ..config import AppError

KINDS = {
    "timeout",
    "worker_crash",
    "worker_startup",
    "resource_limit",
    "invalid_archive",
    "unsupported_format",
    "parsing_error",
    "unsafe_archive",
    "io_error",
    "member_not_found",
}


def redact_debug(value, secrets=()):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = str(value or "")
    hidden = list(secrets)
    hidden.extend(
        v
        for k, v in os.environ.items()
        if any(
            word in k.lower()
            for word in ("token", "password", "secret", "credential", "api_key", "authorization", "cookie")
        )
    )
    for secret in sorted({s for s in hidden if isinstance(s, str) and s}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", value, flags=re.I)
    value = re.sub(
        r"\b(Authorization\s*[:=]\s*)(?:Bearer|Basic)\s+[^\s,;]+", r"\1[REDACTED]", value, flags=re.I
    )
    value = re.sub(
        r"""\b(access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret)(["']?\s*[:=]\s*["']?)[^\s,"'}&]+""",
        r"\1\2[REDACTED]",
        value,
        flags=re.I,
    )
    # Retain line breaks for tracebacks but remove terminal escape/control characters.
    return "".join(c for c in value if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cf", "Cs"})[
        :16384
    ]


class ArchiveError(AppError):
    def __init__(self, kind, message, detail=""):
        self.kind = kind if kind in KINDS else "parsing_error"
        self._debug_detail = detail
        super().__init__("Archive {}: {}".format(self.kind.replace("_", " "), message))

    @property
    def debug_detail(self):
        return self.diagnostics()

    def diagnostics(self, secrets=()):
        # Redact known credentials BEFORE truncating so a long token cannot leak
        # as a partial suffix at the debug output boundary.
        return redact_debug(self._debug_detail, secrets)
