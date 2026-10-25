"""Local game-save discovery, backup and restore. Nothing is synchronised, nothing is deleted, nothing is overwritten
without a preview, an explicit confirmation and an automatic safety backup of what would be replaced."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from ..config import AppError, atomic_json, private_dir, read_json
from ..identity.resolver import normalize
from ..runtime.discovery import prefix_path
from ..runtime.installer import directory_name

NAMESPACE = "save_paths"
MAX_BYTES = 2 * 1024**3
MAX_FILES = 50000
REASONS = {"manual", "pre-reinstall", "pre-prefix-change", "pre-restore"}
FORBIDDEN = {"/", "/home", "/root", "/usr", "/etc", "/var", "/bin", "/lib", "/opt", "/boot", "/dev", "/proc", "/sys", "/tmp"}
WINDOWS_RELATIVE = ["AppData/Roaming", "AppData/Local", "AppData/LocalLow", "Documents", "Documents/My Games", "Saved Games", "My Documents", "My Documents/My Games"]
LINUX_RELATIVE = [".local/share", ".config", ".config/unity3d", ".local/share/Steam/steamapps/compatdata"]


def now():
    return datetime.now(timezone.utc)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_rel(rel):
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise AppError("Unsafe path in backup manifest")
    return p


def walk_files(root, limit_bytes=None, limit_files=None):
    """Regular files under root, without following symlinks; returns (files, skipped, total_bytes)."""
    limit_bytes = MAX_BYTES if limit_bytes is None else limit_bytes
    limit_files = MAX_FILES if limit_files is None else limit_files
    files, skipped, total = [], [], 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for d in list(dirs):
            if os.path.islink(os.path.join(base, d)):
                skipped.append(os.path.relpath(os.path.join(base, d), root) + " (symlinked directory)")
                dirs.remove(d)
        for name in names:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root)
            st = os.lstat(full)
            if not os.path.isfile(full) or os.path.islink(full):
                skipped.append(rel + " (not a regular file)")
                continue
            total += st.st_size
            if total > limit_bytes or len(files) >= limit_files:
                raise AppError("Save location exceeds the size limit ({} GiB / {} files); choose a narrower folder".format(limit_bytes // 1024**3, limit_files))
            files.append((rel, st.st_size))
    return sorted(files), skipped, total


class SaveManager:
    def __init__(self, config, db):
        self.config, self.db = config, db

    # -- locations ---------------------------------------------------------------------------------------------
    def validate_dir(self, path):
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise AppError("Save path must be absolute")
        if p.is_symlink():
            raise AppError("Save path must not be a symlink")
        p = p.resolve()
        if not p.is_dir():
            raise AppError("Save path is not an existing directory")
        if str(p) in FORBIDDEN or p == Path.home().resolve() or p == Path.home().resolve().parent:
            raise AppError("That folder is too broad to treat as a game's saves")
        if not os.access(p, os.R_OK):
            raise AppError("Save path is not readable")
        return p

    def paths(self, game_id):
        return (self.db.get(NAMESPACE, game_id) or {}).get("paths", [])

    def add_path(self, game_id, path, origin="manual"):
        p = self.validate_dir(path)
        record = self.db.get(NAMESPACE, game_id) or {"paths": []}
        if any(x["path"] == str(p) for x in record["paths"]):
            return record
        record["paths"].append({"path": str(p), "origin": origin, "added": now().isoformat()})
        self.db.put(NAMESPACE, game_id, record)
        return record

    def remove_path(self, game_id, path):
        record = self.db.get(NAMESPACE, game_id) or {"paths": []}
        kept = [x for x in record["paths"] if x["path"] != str(Path(path).expanduser())]
        if len(kept) == len(record["paths"]):
            raise AppError("That path is not registered for this game")
        self.db.put(NAMESPACE, game_id, {"paths": kept})

    def discover(self, game_id, title):
        """Candidate save folders with evidence. Uncertain: nothing is chosen for you. Symlinked folders are skipped."""
        keys = {normalize(title)}
        launcher = self.db.get("launchers", game_id) or {}
        if launcher.get("executable"):
            keys.add(normalize(Path(launcher["executable"]).stem))
        for row in self.db.all("aliases"):
            if row["value"]["game_id"] == game_id:
                keys.add(normalize(row["value"]["alias"]))
        keys.discard("")
        roots = []
        pp = prefix_path(self.config, self.db, game_id, launcher.get("prefix"))
        if pp and (pp / "drive_c" / "users").is_dir():
            for user in sorted((pp / "drive_c" / "users").iterdir()):
                if user.name.lower() != "public" and user.is_dir() and not user.is_symlink():
                    roots += [(user / rel, "Wine prefix user profile: " + rel) for rel in WINDOWS_RELATIVE]
        home = Path.home()
        roots += [(home / rel, "Linux user data: " + rel) for rel in LINUX_RELATIVE]
        if launcher.get("working_directory"):
            roots.append((Path(launcher["working_directory"]), "game folder (some games save beside the executable)"))
        found, seen = [], set()
        for root, label in roots:
            if not root.is_dir() or root.is_symlink():
                continue
            try:
                entries = sorted(os.scandir(root), key=lambda e: e.name)[:500]
            except OSError:
                continue
            for e in entries:
                if not e.is_dir(follow_symlinks=False):
                    continue
                n = normalize(e.name)
                match = any(n == k or (len(k) >= 4 and (k in n or n in k) and len(n) >= 4) for k in keys)
                if label.startswith("game folder"):
                    match = n in {"save", "saves", "savedata", "savegames", "profiles"}
                if match and e.path not in seen:
                    seen.add(e.path)
                    try:
                        files, skipped, total = walk_files(e.path, MAX_BYTES * 4)
                    except AppError:
                        files, skipped, total = [], [], -1
                    found.append({"path": e.path, "evidence": ["Folder name matches the game ({})".format(label)], "files": len(files), "bytes": total, "skipped_links": len(skipped),
                                  "confidence": "high" if normalize(e.name) in keys else "medium"})
        return found

    # -- backups ---------------------------------------------------------------------------------------------------
    def backup_root(self, game_id, title=None):
        title = title or (self.db.get("launchers", game_id) or {}).get("title") or game_id
        base = private_dir(self.config.directory("backups") / "saves")
        return private_dir(base / directory_name(game_id, title))

    def backup(self, game_id, reason="manual", title=None, say=None):
        if reason not in REASONS:
            raise AppError("Unknown backup reason")
        sources = self.paths(game_id)
        if not sources:
            raise AppError("No save locations are registered for this game; run 'saves discover {0}' then 'saves add {0} PATH'".format(game_id))
        stamp = now()
        backup_id = "{}-{}-{}".format(stamp.strftime("%Y%m%dT%H%M%SZ"), reason, uuid.uuid4().hex[:6])
        root = self.backup_root(game_id, title)
        stage = root / (".staging-" + backup_id)
        manifest = {"schema": 1, "id": backup_id, "game_id": game_id, "reason": reason, "created_at": stamp.isoformat(), "sources": [], "total_bytes": 0}
        try:
            private_dir(stage)
            free_needed = 0
            plans = []
            for index, src in enumerate(sources):
                p = self.validate_dir(src["path"])
                files, skipped, total = walk_files(p)
                free_needed += total
                plans.append((index, p, files, skipped, total))
            if shutil.disk_usage(root).free < free_needed + 64 * 1024 * 1024:
                raise AppError("Insufficient free space for the save backup")
            for index, p, files, skipped, total in plans:
                entries = []
                for rel, size in files:
                    dst = stage / "data" / str(index) / rel
                    dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(p / rel, dst, follow_symlinks=False)
                    shutil.copystat(p / rel, dst, follow_symlinks=False)
                    digest = sha256_file(dst)
                    if digest != sha256_file(p / rel):
                        raise AppError("A save file changed while it was being copied; close the game and retry")
                    entries.append({"rel": rel, "size": size, "sha256": digest})
                manifest["sources"].append({"index": index, "path": str(p), "files": entries, "skipped": skipped})
                manifest["total_bytes"] += total
            atomic_json(stage / "manifest.json", manifest)
            os.replace(stage, root / backup_id)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        if say:
            say("Backed up {} files ({} bytes) to {}".format(sum(len(s["files"]) for s in manifest["sources"]), manifest["total_bytes"], root / backup_id))
        return manifest | {"location": str(root / backup_id)}

    def backup_if_known(self, game_id, reason, say=None):
        """Safety hook for risky operations. Never blocks them: reports what could not be protected."""
        if not self.paths(game_id):
            if say:
                say("No save locations are registered for {}; no save backup was made (a full prefix backup is separate: 'prefix backup').".format(game_id))
            return None
        try:
            return self.backup(game_id, reason, say=say)
        except AppError as exc:
            if say:
                say("Save backup skipped: {}".format(exc))
            raise

    def list_backups(self, game_id):
        root = self.backup_root(game_id)
        out = []
        for entry in sorted(root.iterdir(), reverse=True):
            if entry.name.startswith(".") or not (entry / "manifest.json").is_file():
                continue
            m = read_json(entry / "manifest.json")
            out.append({"id": m["id"], "reason": m["reason"], "created_at": m["created_at"], "bytes": m["total_bytes"], "files": sum(len(s["files"]) for s in m["sources"])})
        return out

    def manifest(self, game_id, backup_id):
        if not re.fullmatch(r"[0-9TZ]{16}-[a-z-]+-[0-9a-f]{6}", backup_id):
            raise AppError("Invalid backup ID")
        path = self.backup_root(game_id) / backup_id
        if path.is_symlink() or not (path / "manifest.json").is_file():
            raise AppError("Unknown backup")
        m = read_json(path / "manifest.json")
        if m.get("game_id") != game_id:
            raise AppError("Backup belongs to a different game")
        return m, path

    def verify(self, game_id, backup_id):
        m, path = self.manifest(game_id, backup_id)
        bad = []
        for s in m["sources"]:
            for f in s["files"]:
                p = path / "data" / str(s["index"]) / safe_rel(f["rel"])
                if not p.is_file() or sha256_file(p) != f["sha256"]:
                    bad.append(f["rel"])
        return {"id": backup_id, "ok": not bad, "damaged_files": bad}

    # -- restore -----------------------------------------------------------------------------------------------------
    def preview(self, game_id, backup_id):
        m, path = self.manifest(game_id, backup_id)
        report = {"id": backup_id, "created_at": m["created_at"], "reason": m["reason"], "sources": [], "overwrites": 0, "new": 0, "identical": 0}
        for s in m["sources"]:
            dest = Path(s["path"])
            row = {"path": s["path"], "exists": dest.is_dir(), "files": []}
            for f in s["files"]:
                target = dest / safe_rel(f["rel"])
                if target.is_symlink():
                    status = "blocked (symlink)"
                elif not target.exists():
                    status, report["new"] = "new", report["new"] + 1
                elif sha256_file(target) == f["sha256"]:
                    status, report["identical"] = "identical", report["identical"] + 1
                else:
                    status, report["overwrites"] = "overwrites newer/different file", report["overwrites"] + 1
                row["files"].append({"rel": f["rel"], "status": status})
            report["sources"].append(row)
        report["note"] = "Files that exist now but are not in the backup are left untouched. A safety backup of the current saves is made before anything is replaced."
        return report

    def restore(self, game_id, backup_id, confirm=False, say=None):
        pre = self.preview(game_id, backup_id)
        if not confirm:
            return dict(pre, applied=False)
        m, path = self.manifest(game_id, backup_id)
        if self.verify(game_id, backup_id)["damaged_files"]:
            raise AppError("The backup failed its integrity check; refusing to restore from it")
        registered = {p["path"] for p in self.paths(game_id)}
        unregistered = [s["path"] for s in m["sources"] if s["path"] not in registered]
        if unregistered:
            # A manifest alone never decides where files are written.
            raise AppError("This backup restores into folders that are not registered for this game ({}); register them with 'saves add' first".format(", ".join(unregistered)))
        safety = None
        if any(src["exists"] for src in pre["sources"]) and (pre["overwrites"] or pre["new"]):
            safety = self.backup(game_id, "pre-restore", say=say)
        copied = 0
        for s in m["sources"]:
            dest = Path(s["path"])
            self.validate_dir(dest) if dest.exists() else dest.mkdir(parents=True, exist_ok=True, mode=0o700)
            for f in s["files"]:
                rel = safe_rel(f["rel"])
                target = dest / rel
                node = dest
                for part in rel.parts[:-1]:
                    node = node / part
                    if node.is_symlink():
                        raise AppError("Refusing to write through a symlink: " + str(node))
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink():
                    raise AppError("Refusing to overwrite a symlink: " + str(target))
                tmp = target.with_name(".restore-" + uuid.uuid4().hex[:8])
                shutil.copyfile(path / "data" / str(s["index"]) / rel, tmp)
                if sha256_file(tmp) != f["sha256"]:
                    tmp.unlink()
                    raise AppError("Verification failed while restoring " + f["rel"])
                os.replace(tmp, target)
                copied += 1
        return dict(pre, applied=True, restored_files=copied, safety_backup=safety["location"] if safety else None)
