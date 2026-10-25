#!/usr/bin/env python3
"""Dependency-free, user-owned application installation and removal."""

import argparse
import ctypes.util
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

MARKER = "# Managed by Arch Games 2.0"


def write_json(path, value):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def managed_launcher(path):
    if path.is_symlink():
        return os.readlink(path) in {"arch-games", "arch86-games"}
    if not path.exists():
        return True
    if not path.is_file() or path.stat().st_size > 8192:
        return False
    text = path.read_text(errors="replace")
    return MARKER in text or "arch86-games.py" in text and "exec python3 " in text


def installation_lock(function):
    @wraps(function)
    def locked(self, *args, **kwargs):
        self.prepare()
        fd = os.open(self.data / ".app-install.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another application update is active") from None
            return function(self, *args, **kwargs)

    return locked


class ApplicationInstall:
    def __init__(self, home=None, data=None, config=None):
        self.home = Path(home or Path.home())
        self.data = Path(data or os.environ.get("XDG_DATA_HOME", self.home / ".local/share")) / "arch-games"
        self.config = Path(config or os.environ.get("XDG_CONFIG_HOME", self.home / ".config")) / "arch-games"
        self.bin = self.home / ".local/bin"
        self.app = self.data / "app"
        self.previous = self.data / "app.previous"
        self.journal = self.data / ".application-update.json"

    def prepare(self):
        for path in [self.data, self.config, self.bin]:
            if path.is_symlink():
                raise RuntimeError("Installation directory must not be a symlink")
            path.mkdir(parents=True, exist_ok=True)
            if path.stat().st_uid != os.getuid():
                raise RuntimeError("Installation directory must be owned by the current user")
        self.data.chmod(0o700)
        self.config.chmod(0o700)
        for name in ["arch-games", "arch86-games", "arch64-games"]:
            if not managed_launcher(self.bin / name):
                raise RuntimeError("Unrelated launcher already exists: " + name)
        if self.app.is_symlink() or self.previous.is_symlink():
            raise RuntimeError("Application paths must not be symlinks")

    def recover(self):
        if self.journal.is_file():
            if not self.app.exists() and self.previous.exists():
                os.replace(self.previous, self.app)
            op = json.loads(self.journal.read_text())
            name = op.get("stage", "")
            if name.startswith(".app-stage-") and Path(name).name == name:
                path = self.data / name
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
            self.journal.unlink()

    @installation_lock
    def install(self, source):
        self.prepare()
        self.recover()
        source = Path(source).resolve()
        if not (source / "arch_games/__main__.py").is_file() or not (source / "catalog.txt").is_file():
            raise RuntimeError("Source directory does not contain an Arch Games distribution")
        stage = Path(tempfile.mkdtemp(prefix=".app-stage-", dir=self.data))
        write_json(self.journal, {"stage": stage.name})
        try:
            shutil.copytree(
                source / "arch_games",
                stage / "arch_games",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            for name in ["arch86-games.py", "catalog.txt", "install_app.py", "uninstall.sh"]:
                if (source / name).is_file():
                    shutil.copy2(source / name, stage / name)
            if (source / "docs").is_dir():
                shutil.copytree(source / "docs", stage / "docs")
            if (source / "README.md").is_file():
                shutil.copy2(source / "README.md", stage / "README.md")
            bundled = stage / "arch_games/catalog.txt"
            if digest(bundled) != digest(stage / "catalog.txt"):
                raise RuntimeError("Distribution catalog copies differ")
            files = {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
            write_json(
                stage / ".installed.json",
                {
                    "schema": 1,
                    "version": "2.0.0",
                    "files": files,
                    "architecture": platform.machine(),
                    "python": platform.python_version(),
                },
            )
            result = subprocess.run(
                [sys.executable, "-B", str(stage / "arch86-games.py"), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode or result.stdout.strip() != "2.0.0":
                raise RuntimeError("Staged application failed integrity smoke test")
            legacy = self.home / ".local/share/arch86-games/catalog.txt"
            migrated = self.config / "catalog.txt"
            if legacy.is_file() and not migrated.exists():
                shutil.copy2(legacy, migrated)
                migrated.chmod(0o600)
            if self.previous.exists():
                if not (self.previous / ".installed.json").is_file():
                    raise RuntimeError("Unmanaged previous application directory exists")
                shutil.rmtree(self.previous)
            if self.app.exists():
                if not (self.app / ".installed.json").is_file():
                    raise RuntimeError("Refusing to replace an unmanaged app directory")
                os.replace(self.app, self.previous)
            os.replace(stage, self.app)
            # Launcher replacement is atomic; old aliases continue through upgrades.
            launcher = (
                "#!/bin/sh\n"
                + MARKER
                + "\nexec python3 -B "
                + shlex.quote(str(self.app / "arch86-games.py"))
                + ' "$@"\n'
            )
            fd, temp = tempfile.mkstemp(dir=self.bin, prefix=".arch-games-")
            with os.fdopen(fd, "w") as output:
                output.write(launcher)
            os.chmod(temp, 0o755)
            os.replace(temp, self.bin / "arch-games")
            for alias in ["arch86-games", "arch64-games"]:
                temp_alias = self.bin / (".alias-" + uuid.uuid4().hex)
                temp_alias.symlink_to("arch-games")
                os.replace(temp_alias, self.bin / alias)
            self.journal.unlink(missing_ok=True)
            return {
                "installed": str(self.app),
                "launcher": str(self.bin / "arch-games"),
                "settings": str(self.config),
                "migrated_catalog": migrated.is_file(),
            }
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            if not self.app.exists() and self.previous.exists():
                os.replace(self.previous, self.app)
            self.journal.unlink(missing_ok=True)
            raise

    @installation_lock
    def uninstall(self):
        self.prepare()
        self.recover()
        for path in [self.app, self.previous]:
            if path.exists():
                if not (path / ".installed.json").is_file():
                    raise RuntimeError("Refusing to remove unmanaged application files")
                shutil.rmtree(path)
        for name in ["arch86-games", "arch64-games", "arch-games"]:
            path = self.bin / name
            if managed_launcher(path):
                path.unlink(missing_ok=True)
        return {
            "removed": "application and launch aliases",
            "retained": "settings, credentials, library, downloads, prefixes, backups and legacy installation",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--ref", help="Source revision used by the remote bootstrap (informational locally)")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise RuntimeError("Run as the ordinary VM user, not root")
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    if platform.machine().lower() not in {"i386", "i486", "i586", "i686", "pentium4", "x86_64", "amd64"}:
        raise RuntimeError("Supported targets are Linux i686/Pentium4 and x86-64")
    if not args.uninstall and (platform.system() != "Linux" or not shutil.which("pacman")):
        raise RuntimeError("Install on Arch Linux or Arch Linux 32 with Pacman")
    installer = ApplicationInstall()
    if args.uninstall:
        if (
            not args.yes
            and input("Remove Arch Games application, retaining all user data? [y/N] ").lower() != "y"
        ):
            return
        result = installer.uninstall()
    else:
        result = installer.install(args.source)
    print(json.dumps(result, indent=2))
    if not args.uninstall:
        optional = {
            name: bool(shutil.which(name)) for name in ["wine", "wineboot", "dosbox", "glxinfo", "vulkaninfo"]
        }
        optional["libarchive"] = bool(ctypes.util.find_library("archive"))
        print("Optional capabilities: " + json.dumps(optional))
        print("Launch: arch-games | Gallery: arch-games web | Fish: fish_add_path ~/.local/bin")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        print("Installer error: " + str(error), file=sys.stderr)
        raise SystemExit(1)
