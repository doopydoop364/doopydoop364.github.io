"""Transactional local records. No server writes and no credentials."""

from datetime import datetime, timezone
import json
import sqlite3
import os
from pathlib import Path
from ..config import AppError
from .migrations import migrate


class Database:
    def __init__(self, path):
        if str(path) != ":memory:":
            path = Path(path)
            if path.is_symlink():
                raise AppError("Local state database must not be a symlink")
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            if path.stat().st_uid != os.getuid():
                raise AppError("Local state database must be owned by this user")
            path.chmod(0o600)
        self.connection = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS records (namespace TEXT, key TEXT, value TEXT, updated TEXT, PRIMARY KEY(namespace,key))"
        )
        self.backup_path = migrate(self.connection, None if str(path) == ":memory:" else path)

    def put(self, namespace, key, value):
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                (namespace, str(key), json.dumps(value), now),
            )
        return now

    def get(self, namespace, key, default=None):
        row = self.connection.execute(
            "SELECT value FROM records WHERE namespace=? AND key=?", (namespace, str(key))
        ).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, namespace):
        rows = self.connection.execute(
            "SELECT key,value,updated FROM records WHERE namespace=? ORDER BY key", (namespace,)
        )
        return [{"key": k, "value": json.loads(v), "updated": u} for k, v, u in rows]

    def mapping(self, namespace):
        rows = self.connection.execute("SELECT key,value FROM records WHERE namespace=?", (namespace,))
        return {key: json.loads(value) for key, value in rows}

    def delete(self, namespace, key):
        with self.connection:
            self.connection.execute("DELETE FROM records WHERE namespace=? AND key=?", (namespace, str(key)))

    def close(self):
        self.connection.close()
