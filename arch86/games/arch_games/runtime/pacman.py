"""Read-only queries against the local Pacman database. This module never installs, removes or refreshes anything and never
calls sudo; package ownership is the trust anchor for native launchers."""

from pathlib import Path
import re
import shlex
import subprocess

PACKAGE = re.compile(r"^[a-zA-Z0-9@_+][a-zA-Z0-9@._+\-]*\Z")
MAX_FILES = 300_000
FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")


class PacmanService:
    """Thin, injectable wrapper (tests pass a fake `run`). All calls use argument arrays and a timeout."""

    def __init__(self, run=None, timeout=30):
        if run is None:
            from ..catalog.pacman import default_run

            run = default_run()  # the same hook the catalog uses, so one fake package database serves every caller
        self.run, self.timeout = run, timeout
        self.unavailable = False

    def _query(self, *args):
        """(returncode | None, stdout). None means pacman itself could not be run."""
        try:
            result = self.run(["pacman", *args], capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired):
            self.unavailable = True
            return None, ""
        return result.returncode, (result.stdout or "")

    @staticmethod
    def check_name(package):
        if not isinstance(package, str) or not PACKAGE.match(package) or len(package) > 128:
            raise ValueError("Invalid package name")
        return package

    def version(self, package):
        """Installed version string, or None when the package is not installed (or pacman is unavailable)."""
        code, out = self._query("-Q", "--", self.check_name(package))
        if code != 0:
            return None
        parts = out.split()
        return parts[1] if len(parts) >= 2 and parts[0] == package else None

    def installed_versions(self):
        """{package: version} for everything installed (one query)."""
        code, out = self._query("-Q")
        versions = {}
        if code == 0:
            for line in out.splitlines()[:200_000]:
                parts = line.split()
                if len(parts) >= 2:
                    versions[parts[0]] = parts[1]
        return versions

    def files(self, package):
        """Absolute paths owned by an installed package (directories excluded); [] if not installed."""
        code, out = self._query("-Ql", "--", self.check_name(package))
        if code != 0:
            return []
        paths = []
        for line in out.splitlines()[:MAX_FILES]:
            _, _, path = line.partition(" ")
            if path.startswith("/") and not path.endswith("/") and "\0" not in path:
                paths.append(path)
        return paths

    def owner(self, path):
        """Package owning this exact path (a path, not a pattern), or None."""
        path = str(path)
        if not path.startswith("/") or "\0" in path:
            return None
        code, out = self._query("-Qoq", "--", path)
        name = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else None
        return name if name and PACKAGE.match(name) else None


def parse_desktop_entry(text):
    """Main [Desktop Entry] group of a .desktop file as a dict (no evaluation of anything)."""
    entry, inside = {}, False
    for line in text.splitlines()[:400]:
        line = line.strip()
        if line.startswith("["):
            inside = line == "[Desktop Entry]"
            continue
        if inside and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            if "[" not in key:  # ignore localized keys
                entry[key.strip()] = value.strip()
    return entry


def parse_exec(exec_line):
    """Desktop-entry Exec -> (program, [arguments], {env hints}). Pure parsing with shlex: no shell, nothing executed.

    Field codes (%U, %f, ...) are dropped, a leading `env VAR=value` is returned as hints and never applied automatically."""
    try:
        tokens = shlex.split(FIELD_CODES.sub("", exec_line.replace("%%", "\0")).replace("\0", "%"))
    except ValueError:
        return None, [], {}
    env = {}
    if tokens and tokens[0] in {"env", "/usr/bin/env"}:
        tokens = tokens[1:]
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            name, _, value = tokens.pop(0).partition("=")
            env[name] = value
    if not tokens:
        return None, [], env
    return tokens[0], tokens[1:], env


def read_text(path, limit=65536):
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""
