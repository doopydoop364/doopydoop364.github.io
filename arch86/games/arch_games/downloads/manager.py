"""Persistent serial queue with safe, validator-aware resumable transfers."""

from datetime import datetime, timezone
import fcntl
import http.client
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from ..config import AppError
from ..gamevault.client import Client, entity_id
from .verification import verify

CHUNK = 256 * 1024
RESERVE = 64 * 1024 * 1024


def filename(value):
    value = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"[^A-Za-z0-9._ -]", "_", value).strip(" .")[:160]
    return value if value and value not in {".", ".."} else "game.archive"


def positive_size(value):
    if value is None or value == "":
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or isinstance(value, str)
        and not value.isdecimal()
    ):
        raise AppError("Invalid download size metadata")
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        raise AppError("Invalid download size metadata") from None
    if number < 0 or number > 2**63 - 1:
        raise AppError("Download size metadata is out of range")
    return number


class DownloadManager:
    def __init__(self, config, db, client=None, disk_usage=shutil.disk_usage):
        self.config, self.db = config, db
        self.root = config.directory("downloads")
        self.client = client
        self.disk_usage = disk_usage

    def get(self, ident):
        job = self.db.get("downloads", ident)
        if not job:
            raise AppError("Unknown download ID")
        return job

    def path(self, job, partial=False):
        name = job["filename"] + (".part" if partial else "")
        if name != Path(name).name or name in {".", ".."}:
            raise AppError("Unsafe download filename in state")
        path = self.root / name
        if path.is_symlink():
            raise AppError("Download path is a symlink")
        return path

    def add(self, game, checksum=None, max_bytes=32 * 1024**3):
        if game.source != "gamevault":
            raise AppError("Only authorized GameVault library games can be queued")
        if checksum and not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise AppError("Expected SHA-256 must contain 64 hexadecimal digits")
        remote = game.metadata.get("remote", {})
        version = game.metadata.get("selected_version") or remote
        game_id = entity_id(str(game.id).split(":", 1)[-1].split("@", 1)[0])
        version_id = version.get("id") if game.metadata.get("selected_version") else None
        ident = uuid.uuid4().hex
        size = positive_size(version.get("size"))
        if max_bytes <= 0 or size is not None and size > max_bytes:
            raise AppError("Download exceeds configured size limit")
        job = {
            "id": ident,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "game_id": game.id,
            "title": game.title,
            "remote_id": game_id,
            "version_id": version_id,
            "server_url": self.config.values.get("server_url"),
            "account": self.config.values.get("account"),
            "state": "queued",
            "received": 0,
            "expected_size": size,
            "checksum": checksum,
            "max_bytes": max_bytes,
            "filename": ident + "-" + filename(version.get("file_path") or game.title + ".archive"),
            "validator": None,
            "error": None,
            "game": game.to_dict(),
        }
        self.db.put("downloads", ident, job)
        self.db.put("events", uuid.uuid4().hex, {"operation": "download queued", "id": ident})
        return job

    def space(self, amount):
        if self.disk_usage(self.root).free < amount + RESERVE:
            raise AppError("Insufficient free disk space (64 MiB safety reserve)")

    def retry(self, ident, restart=False):
        job = self.get(ident)
        if job["state"] == "completed":
            raise AppError("Download is already complete")
        with self.lock():
            if restart:
                self.path(job, True).unlink(missing_ok=True)
                job.update(received=0, validator=None)
            self.db.delete("download_cancel", ident)
            job.update(state="queued", error=None)
            self.db.put("downloads", ident, job)
        return job

    def cancel(self, ident, cleanup=False):
        job = self.get(ident)
        if job["state"] == "completed":
            raise AppError("Download already completed; retained archive will not be removed")
        self.db.put("download_cancel", ident, {"cleanup": cleanup})
        try:
            with self.lock():
                if cleanup:
                    self.path(job, True).unlink(missing_ok=True)
                job.update(
                    state="cancelled",
                    received=self.path(job, True).stat().st_size if self.path(job, True).exists() else 0,
                )
                self.db.put("downloads", ident, job)
        except AppError:
            # Active worker observes the separate cancellation flag on the next chunk.
            pass

    def lock(self):
        return QueueLock(self.root / ".queue.lock")

    def run(self, progress=None):
        with self.lock():
            # A killed process may leave running jobs. Recover them as resumable queue items.
            for row in self.db.all("downloads"):
                job = row["value"]
                if job["state"] == "running":
                    job["state"] = "queued"
                    self.db.put("downloads", row["key"], job)
            queued = sorted(
                self.db.all("downloads"), key=lambda row: row["value"].get("created_at", row["updated"])
            )
            for row in queued:
                job = self.get(row["key"])
                if job["state"] != "queued":
                    continue
                cancel = self.db.get("download_cancel", job["id"])
                if cancel:
                    if cancel.get("cleanup"):
                        self.path(job, True).unlink(missing_ok=True)
                        job["received"] = 0
                    job["state"] = "cancelled"
                    self.db.put("downloads", job["id"], job)
                    continue
                try:
                    self.transfer(job, progress)
                except KeyboardInterrupt:
                    job.update(state="paused", error="Interrupted; use download retry to resume")
                    self.db.put("downloads", job["id"], job)
                    raise
                except (AppError, OSError, http.client.HTTPException) as exc:
                    job.update(
                        state="failed",
                        error=str(exc)
                        if isinstance(exc, AppError)
                        else "Transfer interrupted; retry to resume",
                    )
                    self.db.put("downloads", job["id"], job)

    def transfer(self, job, progress=None):
        if job["server_url"] != self.config.values.get("server_url") or job.get(
            "account"
        ) != self.config.values.get("account"):
            raise AppError("Download belongs to a different server/account; reconnect before resuming")
        client = self.client or Client(self.config, self.db)
        part, target = self.path(job, True), self.path(job)
        if target.exists():
            # Recovery after rename succeeded but state commit was interrupted.
            verification = verify(target, job["expected_size"], job["checksum"])
            job.update(verification, state="completed", received=target.stat().st_size)
            self.db.put("downloads", job["id"], job)
            return
        for attempt in range(3):
            try:
                offset = part.stat().st_size if part.exists() else 0
                if part.exists() and job["expected_size"] is not None and offset == job["expected_size"]:
                    verification = verify(part, job["expected_size"], job["checksum"])
                    os.replace(part, target)
                    job.update(verification, state="completed", received=offset, error=None)
                    self.db.put("downloads", job["id"], job)
                    if progress:
                        progress(dict(job))
                    return
                validator = job.get("validator")
                if validator and (
                    not isinstance(validator, str)
                    or len(validator) > 1024
                    or not all(32 <= ord(c) <= 126 for c in validator)
                ):
                    raise AppError("Invalid download validator; retry --restart")
                if offset and not validator:
                    part.unlink()
                    offset = 0
                headers = {"Range": "bytes={}-".format(offset), "If-Range": validator} if offset else {}
                expected = job["expected_size"]
                if expected is not None and offset > expected:
                    raise AppError("Partial file exceeds expected size; retry --restart")
                self.space(max((expected or CHUNK) - offset, CHUNK))
                with client.open(
                    client.download_path(job["remote_id"], job["version_id"]), headers=headers
                ) as response:
                    code = response.status
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise AppError("Encoded downloads cannot be safely resumed")
                    length = positive_size(response.headers.get("Content-Length"))
                    if code == 206:
                        match = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                        )
                        if not match:
                            raise AppError("Invalid resumed Content-Range")
                        start, end, total = map(int, match.groups())
                        if (
                            start != offset
                            or end < start
                            or end >= total
                            or length is not None
                            and length != end - start + 1
                        ):
                            raise AppError("Server returned a mismatched download range")
                        if expected is not None and total != expected:
                            raise AppError("Game changed on server; queue its new version")
                        expected = total
                    elif code == 200:
                        offset = 0
                        if length is not None:
                            if expected is not None and length != expected:
                                raise AppError("Server size differs from library metadata")
                            expected = length
                    else:
                        raise AppError("Unexpected download response")
                    if expected is not None and expected > job["max_bytes"]:
                        raise AppError("Download exceeds configured byte limit")
                    etag = response.headers.get("ETag")
                    new_validator = (
                        etag if etag and not etag.startswith("W/") else response.headers.get("Last-Modified")
                    )
                    if new_validator and (
                        len(new_validator) > 1024 or not all(32 <= ord(c) <= 126 for c in new_validator)
                    ):
                        raise AppError("Server returned an invalid download validator")
                    if code == 206 and validator and new_validator and new_validator != validator:
                        raise AppError("Download validator changed; retry --restart")
                    self.space(max((expected or CHUNK) - offset, CHUNK))
                    job.update(
                        state="running", received=offset, validator=new_validator, expected_size=expected
                    )
                    self.db.put("downloads", job["id"], job)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_APPEND if offset else os.O_TRUNC)
                    last_save = 0
                    with os.fdopen(os.open(part, flags, 0o600), "ab" if offset else "wb") as stream:
                        while True:
                            cancel = self.db.get("download_cancel", job["id"])
                            if cancel:
                                job["state"] = "cancelled"
                                break
                            self.space(CHUNK)
                            chunk = response.read(CHUNK)
                            if not chunk:
                                break
                            received = job["received"] + len(chunk)
                            if received > job["max_bytes"] or expected is not None and received > expected:
                                raise AppError("Download exceeds expected or configured size")
                            stream.write(chunk)
                            job["received"] = received
                            if time.monotonic() - last_save > 0.5:
                                self.db.put("downloads", job["id"], job)
                                if progress:
                                    progress(dict(job))
                                last_save = time.monotonic()
                        stream.flush()
                        os.fsync(stream.fileno())
                    if job["state"] == "cancelled":
                        if cancel.get("cleanup"):
                            part.unlink(missing_ok=True)
                            job["received"] = 0
                        self.db.put("downloads", job["id"], job)
                        return
                verification = verify(part, expected, job["checksum"])
                os.replace(part, target)
                job.update(verification, state="completed", error=None)
                self.db.put("downloads", job["id"], job)
                self.db.put(
                    "events",
                    uuid.uuid4().hex,
                    {"operation": "download completed", "id": job["id"], "verification": job["verification"]},
                )
                if progress:
                    progress(dict(job))
                return
            except (OSError, http.client.HTTPException):
                if attempt == 2:
                    raise
                time.sleep(0.2 * (attempt + 1))


class QueueLock:
    def __init__(self, path):
        self.path = path
        self.stream = None

    def __enter__(self):
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.stream = os.fdopen(fd, "w")
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close()
            raise AppError("Another download/installation operation is active") from None
        return self

    def __exit__(self, *args):
        fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()
