"""Recoverable per-game extraction installs; no executable runs during installation."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import uuid

from ..compatibility.archives import ArchiveLimits, extract_archive, inspect_archive
from ..config import AppError, atomic_json, read_json
from ..downloads.manager import QueueLock, RESERVE
from ..downloads.verification import sha256

MANIFEST = ".arch-games-manifest.json"


def child(root, name):
    if not name or name != Path(name).name or name in {".", ".."}:
        raise AppError("Unsafe managed directory name")
    path = Path(root) / name
    if path.is_symlink():
        raise AppError("Managed directory is a symlink")
    return path


def directory_name(ident, title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "game"
    return slug + "--" + hashlib.sha256(ident.encode()).hexdigest()[:16]


class Installer:
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.root = config.directory("installed")
        self.backups = config.directory("backups")

    def lock(self):
        return QueueLock(self.root / ".operations.lock")

    def recover(self):
        """Complete committed renames or roll back incomplete staging operations."""
        for row in self.db.all("install_transactions"):
            op = row["value"]
            dest = child(self.root, op["destination"])
            stage = child(self.root, op["stage"])
            backup = child(self.backups, op["backup"])
            manifest = read_json(dest / MANIFEST) if dest.is_dir() and (dest / MANIFEST).is_file() else {}
            if manifest.get("transaction") == op["transaction"]:
                self.db.put("installs", op["game_id"], manifest)
            elif not dest.exists() and backup.exists():
                os.replace(backup, dest)
            if stage.exists():
                shutil.rmtree(stage)
            self.db.delete("install_transactions", row["key"])

    def install(self, game, archive, reinstall=False, timeout=600, progress=None):
        archive = Path(archive).resolve()
        if not archive.is_file():
            raise AppError("Installation archive is not a regular file")
        if not 1 <= timeout <= 3600:
            raise AppError("Archive timeout must be between 1 and 3600 seconds")
        with self.lock():
            self.recover()
            previous = self.db.get("installs", game.id)
            if previous and not reinstall:
                raise AppError("Game is already installed; use --reinstall to preserve a backup and replace it")
            if progress:
                progress("Inspecting archive")
            listing = inspect_archive(archive, ArchiveLimits(seconds=min(timeout, 120)))
            if any(entry["name"] == MANIFEST for entry in listing["entries"]):
                raise AppError("Archive collides with reserved installation manifest")
            if shutil.disk_usage(self.root).free < listing["expanded_bytes"] + RESERVE:
                raise AppError("Insufficient free space for extraction")
            transaction = uuid.uuid4().hex
            name = previous["directory"] if previous else directory_name(game.id, game.title)
            destination = child(self.root, name)
            if destination.exists() and not previous:
                raise AppError("Unmanaged directory already occupies the installation destination")
            operation = {"transaction": transaction, "game_id": game.id, "destination": name,
                         "stage": ".staging-" + transaction, "backup": "installed-" + transaction}
            stage = child(self.root, operation["stage"])
            backup = child(self.backups, operation["backup"])
            stage.mkdir(mode=0o700)
            self.db.put("install_transactions", transaction, operation)
            try:
                if progress:
                    progress("Extracting into private staging directory; Ctrl+C cancels")
                report = extract_archive(archive, stage, ArchiveLimits(seconds=timeout))
                binaries = [r for r in report["entries"] if r.get("executable")]
                manifest = {"schema": 1, "game_id": game.id, "game": game.to_dict(), "directory": name,
                            "transaction": transaction, "archive_sha256": sha256(archive),
                            "archive_path": str(archive), "expanded_bytes": report["expanded_bytes"],
                            "files": report["entries"], "executables": binaries,
                            "dependencies": sorted({dll for r in binaries for dll in r["executable"].get("imports", [])}),
                            "previous_backup": operation["backup"] if previous else None}
                atomic_json(stage / MANIFEST, manifest)
                if destination.exists():
                    os.replace(destination, backup)
                os.replace(stage, destination)
                self.db.put("installs", game.id, manifest)
                self.db.delete("install_transactions", transaction)
                self.db.put("events", transaction, {"operation": "game installed", "game_id": game.id, "backup": manifest["previous_backup"]})
                if progress:
                    progress("Installed; launch requires a separate explicit command")
                return manifest
            except BaseException:
                self.recover()
                raise

    def uninstall(self, game_id, purge=False):
        with self.lock():
            self.recover()
            manifest = self.db.get("installs", game_id)
            if not manifest:
                raise AppError("Game is not managed by Arch Games")
            dest = child(self.root, manifest["directory"])
            backup = child(self.backups, "removed-" + uuid.uuid4().hex)
            if dest.exists():
                os.replace(dest, backup)
            self.db.delete("installs", game_id)
            self.db.put("events", uuid.uuid4().hex, {"operation": "game removed", "game_id": game_id,
                                                    "backup": str(backup) if not purge else None})
            if purge and backup.exists():
                shutil.rmtree(backup)
            return {"removed": game_id, "backup": str(backup) if not purge else None,
                    "archives_and_prefixes": "retained"}
