"""Explicit isolated Wine environments. No DLL installers or package installation."""
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from ..compatibility.graphics import command
from ..config import AppError, atomic_json, read_json
from ..downloads.manager import QueueLock, RESERVE
from ..platform import architecture
from .environment import environment
from .installer import child, directory_name


class PrefixManager:
    def __init__(self, config, db, run=subprocess.run, which=shutil.which):
        self.config, self.db, self.run, self.which = config, db, run, which
        self.root = config.directory("prefixes")

    def lock(self):
        return QueueLock(self.root / ".operations.lock")

    def path(self, ident):
        record = self.db.get("prefixes", ident)
        if not record:
            raise AppError("No isolated prefix for this game; run prefix create")
        return child(self.root, record["directory"])

    def create(self, ident, arch="win64", custom=None):
        if arch not in {"win32", "win64"}:
            raise AppError("Prefix architecture must be win32 or win64")
        if arch == "win64" and architecture() == "i686":
            raise AppError("A 32-bit operating system cannot create a 64-bit Wine prefix")
        wineboot = self.which("wineboot")
        wine = self.which("wine")
        if not wineboot or not wine:
            raise AppError("Wine/wineboot is missing; install an appropriate supported runtime explicitly")
        with self.lock():
            if self.db.get("prefixes", ident):
                raise AppError("Prefix already exists; check it or remove it explicitly")
            name = directory_name(ident, ident)
            path = child(self.root, name)
            if path.exists():
                raise AppError("Unmanaged prefix directory already exists")
            env = environment(custom)
            env.update(WINEPREFIX=str(path), WINEARCH=arch, WINEDLLOVERRIDES="mscoree,mshtml=")
            path.mkdir(mode=0o700)
            version = command([wine, "--version"], self.run).strip()
            record = {"game_id": ident, "directory": name, "requested_architecture": arch,
                      "wine_version": version, "state": "creating", "environment": custom or {}}
            self.db.put("prefixes", ident, record)
            log = self.config.directory("logs") / ("prefix-" + uuid.uuid4().hex + ".log")
            try:
                with log.open("wb") as output:
                    result = self.run([wineboot, "-u"], env=env, stdout=output, stderr=subprocess.STDOUT, timeout=120)
                if result.returncode:
                    raise AppError("Wine prefix initialization failed; see local runtime log")
                health = self.check(ident)
                if not health["healthy"]:
                    raise AppError("Wine did not create a complete prefix; inspect local runtime log")
                if health["architecture"] != "unknown" and health["architecture"] != arch:
                    raise AppError("Wine created a different prefix architecture; inspect prefix check")
                record.update(state="ready", detected_architecture=health["architecture"], log=str(log))
                atomic_json(path / ".arch-games-prefix.json", record)
                self.db.put("prefixes", ident, record)
                self.db.put("events", uuid.uuid4().hex, {"operation": "prefix created", "game_id": ident})
                return record
            except BaseException:
                record.update(state="failed", log=str(log))
                self.db.put("prefixes", ident, record)
                raise

    def check(self, ident):
        path = self.path(ident)
        complete = path.is_dir() and all((path / f).is_file() for f in ["system.reg", "user.reg"]) and (path / "drive_c").is_dir()
        arch = "unknown"
        if (path / "system.reg").is_file():
            with (path / "system.reg").open("rb") as stream:
                header = stream.read(4096)
            if b"#arch=win64" in header:
                arch = "win64"
            elif b"#arch=win32" in header:
                arch = "win32"
        return {"game_id": ident, "path": str(path), "healthy": complete, "architecture": arch,
                "record": self.db.get("prefixes", ident), "dependencies": "No automatic runtime component installation"}

    def backup(self, ident):
        with self.lock():
            source = self.path(ident)
            needed = sum(p.stat().st_size for p in source.rglob("*") if p.is_file() and not p.is_symlink())
            root = self.config.directory("backups")
            if shutil.disk_usage(root).free < needed + RESERVE:
                raise AppError("Insufficient space for prefix backup")
            target = child(root, "prefix-" + uuid.uuid4().hex)
            try:
                shutil.copytree(source, target, symlinks=True)
            except BaseException:
                if target.exists():
                    shutil.rmtree(target)
                raise
            self.db.put("events", uuid.uuid4().hex, {"operation": "prefix backup", "game_id": ident, "path": str(target)})
            return {"backup": str(target), "note": "Stop games and Wine processes before backing up a prefix"}

    def remove(self, ident, purge=False):
        with self.lock():
            path = self.path(ident)
            target = child(self.config.directory("backups"), "removed-prefix-" + uuid.uuid4().hex)
            if path.exists():
                os.replace(path, target)
            self.db.delete("prefixes", ident)
            if purge and target.exists():
                shutil.rmtree(target)
            self.db.put("events", uuid.uuid4().hex, {"operation": "prefix removed", "game_id": ident, "purged": purge})
            return {"removed": ident, "backup": str(target) if not purge else None}
