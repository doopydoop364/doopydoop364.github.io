"""Explicit isolated Wine environments. No DLL installers or package installation."""

import os
import shutil
import subprocess
import stat
import time
from datetime import datetime, timezone
import uuid

from ..compatibility.graphics import command
from ..config import AppError, atomic_json, read_json
from ..downloads.manager import QueueLock, RESERVE
from ..platform import architecture
from .environment import environment
from .installer import child, directory_name


class PrefixManager:
    def __init__(
        self, config, db, run=subprocess.run, which=shutil.which, clock=time.monotonic, sleep=time.sleep
    ):
        self.config, self.db, self.run, self.which = config, db, run, which
        self.root = config.directory("prefixes")
        self.clock, self.sleep = clock, sleep

    def lock(self):
        return QueueLock(self.root / ".operations.lock")

    def path(self, ident):
        record = self.db.get("prefixes", ident)
        if not record:
            raise AppError("No isolated prefix for this game; run prefix create")
        return child(self.root, record["directory"])

    def _ready(self, ident, record, health, recovered=False):
        record = dict(
            record,
            state="ready",
            detected_architecture=health["architecture"],
            verified_at=datetime.now(timezone.utc).isoformat(),
            verification_scope="structural prefix files; no game executed",
        )
        if recovered:
            record["recovered_at"] = record["verified_at"]
            record["recovered_from_state"] = health["record"].get("state")
        # Only our metadata changes. Registry, drive mappings, game files, original
        # failure details, log, architecture and instance identity remain intact.
        path = self.path(ident)
        marker = path / ".arch-games-prefix.json"
        previous = read_json(marker)
        if not isinstance(previous, dict):
            raise AppError("Prefix metadata must be an object; existing data retained")
        record = dict(previous, **record)
        atomic_json(marker, record)
        self.db.put("prefixes", ident, record)
        self.db.put(
            "events",
            uuid.uuid4().hex,
            {"operation": "prefix recovered" if recovered else "prefix created", "game_id": ident},
        )
        return record

    def _wait_healthy(self, ident, deadline):
        previous = None
        attempts = 0
        attempt_limit = max(2, int(max(deadline - self.clock(), 0) * 10) + 2)
        while True:
            health = self.check(ident)
            attempts += 1
            snapshot = health["registry_snapshot"]
            if health["healthy"] and snapshot == previous:
                health["verification_attempts"] = attempts
                return health
            if health["architecture_mismatch"]:
                break
            remaining = deadline - self.clock()
            if remaining <= 0 or attempts >= attempt_limit:
                break
            previous = snapshot if health["healthy"] else None
            self.sleep(min(0.1, remaining))
        raise AppError(
            "Prefix verification failed: {}. Data retained; run prefix check or prefix recover".format(
                "; ".join(health["issues"])
                if not health["healthy"]
                else "files did not stabilize before the deadline"
            )
        )

    def recover(self, ident, timeout=5):
        if not 0 < timeout <= 120:
            raise AppError("Recovery timeout must be between 1 and 120 seconds")
        with self.lock():
            health = self._wait_healthy(ident, self.clock() + timeout)
            return self._ready(ident, health["record"], health, recovered=True)

    def create(self, ident, arch="win64", custom=None, timeout=120):
        if arch not in {"win32", "win64"}:
            raise AppError("Prefix architecture must be win32 or win64")
        if not 0 < timeout <= 600:
            raise AppError("Initialization timeout must be between 1 and 600 seconds")
        if arch == "win64" and architecture() == "i686":
            raise AppError("A 32-bit operating system cannot create a 64-bit Wine prefix")
        with self.lock():
            existing = self.db.get("prefixes", ident)
            if existing:
                if existing.get("requested_architecture") != arch:
                    raise AppError("Existing prefix has a different requested architecture; data retained")
                if existing.get("state") in {"failed", "creating"}:
                    health = self._wait_healthy(ident, self.clock() + min(timeout, 5))
                    return self._ready(ident, existing, health, recovered=True)
                raise AppError("Prefix already exists; use prefix check (or prefix recover to reverify)")
            wineboot, wine, wineserver = (self.which(name) for name in ["wineboot", "wine", "wineserver"])
            if not all([wineboot, wine, wineserver]):
                raise AppError(
                    "Wine/wineboot/wineserver is missing; install an appropriate runtime explicitly"
                )
            name = directory_name(ident, ident)
            path = child(self.root, name)
            if path.exists():
                raise AppError("Unmanaged prefix directory already exists; data retained")
            env = environment(custom)
            env.update(WINEPREFIX=str(path), WINEARCH=arch, WINEDLLOVERRIDES="mscoree,mshtml=")
            version = command([wine, "--version"], self.run).strip()
            log = self.config.directory("logs") / ("prefix-" + uuid.uuid4().hex + ".log")
            path.mkdir(mode=0o700)
            record = {
                "instance_id": uuid.uuid4().hex,
                "game_id": ident,
                "directory": name,
                "requested_architecture": arch,
                "wine_version": version,
                "state": "creating",
                "environment": custom or {},
                "log": str(log),
                "initialization": {"stage": "wineboot"},
            }
            self.db.put("prefixes", ident, record)
            deadline = self.clock() + timeout

            def remaining(cap):
                seconds = min(cap, deadline - self.clock())
                if seconds <= 0:
                    raise AppError(
                        "Prefix initialization deadline exceeded; data retained; run prefix recover"
                    )
                return seconds

            try:
                with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                    result = self.run(
                        [wineboot, "-u"],
                        env=env,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        timeout=remaining(timeout),
                    )
                    record["initialization"]["wineboot_exit"] = result.returncode
                    if result.returncode:
                        raise AppError(
                            "wineboot exited with status {}; inspect the prefix log; data retained".format(
                                result.returncode
                            )
                        )
                    record["initialization"]["stage"] = "wineserver_wait"
                    self.db.put("prefixes", ident, record)
                    # Wine's registry is persisted by wineserver, potentially after
                    # wineboot exits. Wait for this prefix only; never kill/reset it.
                    result = self.run(
                        [wineserver, "-w"],
                        env=env,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        timeout=remaining(30),
                    )
                    record["initialization"]["wineserver_exit"] = result.returncode
                    if result.returncode:
                        raise AppError(
                            "wineserver wait exited with status {}; data retained; run prefix check or prefix recover".format(
                                result.returncode
                            )
                        )
                record["initialization"]["stage"] = "file_verification"
                self.db.put("prefixes", ident, record)
                health = self._wait_healthy(ident, min(deadline, self.clock() + 5))
                record["initialization"].update(
                    stage="complete", verification_attempts=health["verification_attempts"]
                )
                return self._ready(ident, record, health)
            except BaseException as exc:
                # An event-log error after publishing ready must not poison a valid prefix.
                if self.db.get("prefixes", ident, {}).get("state") != "ready":
                    failure = dict(record, state="failed")
                    failure["last_failure"] = {
                        "stage": record["initialization"]["stage"],
                        "kind": "timeout"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "interrupted"
                        if isinstance(exc, KeyboardInterrupt)
                        else "verification"
                        if record["initialization"]["stage"] == "file_verification"
                        else "runtime_error",
                        "at": datetime.now(timezone.utc).isoformat(),
                        "exception_type": type(exc).__name__,
                        "errno": getattr(exc, "errno", None),
                        "issues": self.check(ident)["issues"],
                    }
                    self.db.put("prefixes", ident, failure)
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise AppError(
                        "Prefix initialization timed out during {}; data retained; run prefix check or prefix recover".format(
                            record["initialization"]["stage"]
                        )
                    ) from None
                raise

    @staticmethod
    def _registry(path):
        try:
            # Wine permits relocated registry files and drive_c symlinks. Read
            # only a regular-file prefix and never write through these links.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    return None, None, "not a regular file"
                header = os.read(fd, 4096)
            finally:
                os.close(fd)
            if not header.startswith(b"WINE REGISTRY Version 2\n"):
                return None, None, "invalid or incomplete registry header"
            arch = next(
                (
                    line[6:].decode("ascii")
                    for line in header.splitlines()
                    if line in {b"#arch=win32", b"#arch=win64"}
                ),
                None,
            )
            if arch is None:
                return None, None, "architecture marker missing"
            return arch, [info.st_ino, info.st_size, info.st_mtime_ns], None
        except FileNotFoundError:
            return None, None, "missing"
        except OSError:
            return None, None, "unreadable or unsafe file type"

    def check(self, ident):
        record = self.db.get("prefixes", ident)
        if not record:
            raise AppError("No isolated prefix for this game; run prefix create")
        path = child(self.root, record["directory"])
        issues, architectures, snapshots = [], [], []
        for name in ["system.reg", "user.reg"]:
            arch, snapshot, error = self._registry(path / name)
            if error:
                issues.append(name + ": " + error)
            architectures.append(arch)
            snapshots.append(snapshot)
        if not (path / "drive_c").is_dir():
            issues.append("drive_c: missing or unsafe directory")
        known = {arch for arch in architectures if arch}
        expected = record.get("requested_architecture")
        mismatch = len(known) > 1 or any(arch != expected for arch in known)
        if expected not in {"win32", "win64"}:
            issues.append("Prefix record has no supported requested architecture")
        if mismatch:
            issues.append("Registry architecture mismatch (requested {})".format(expected))
        arch = next(iter(known)) if len(known) == 1 else "unknown"
        healthy = not issues
        return {
            "game_id": ident,
            "path": str(path),
            "healthy": healthy,
            "architecture": arch,
            "architecture_mismatch": mismatch,
            "issues": issues,
            "registry_snapshot": snapshots,
            "record": record,
            "recovery_available": healthy and record.get("state") != "ready",
            "next_action": "prefix recover " + ident if healthy and record.get("state") != "ready" else None,
            "health_scope": "Structural files and architecture only; gameplay not verified",
            "dependencies": "No automatic runtime component installation",
        }

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
            self.db.put(
                "events",
                uuid.uuid4().hex,
                {"operation": "prefix backup", "game_id": ident, "path": str(target)},
            )
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
            self.db.put(
                "events", uuid.uuid4().hex, {"operation": "prefix removed", "game_id": ident, "purged": purge}
            )
            return {"removed": ident, "backup": str(target) if not purge else None}
