"""Versioned, transactional, idempotent schema migrations for the local state database.

Version 1 is the Arch Games 2.0 layout (a single ``records`` table, no version marker).
Existing namespaces are never rewritten; new features use new namespaces.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from ..config import AppError

CURRENT_VERSION = 2


def _migrate_2(connection):
    # 3.0 additions: namespace lookup index. New namespaces need no DDL.
    connection.execute("CREATE INDEX IF NOT EXISTS records_namespace ON records(namespace)")


MIGRATIONS = {2: _migrate_2}


def schema_version(connection):
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if not row:
        return 1
    value = connection.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    return int(value[0]) if value else 1


def validate(connection):
    """Cheap integrity gate run before and after migrating."""
    result = connection.execute("PRAGMA quick_check").fetchone()
    if not result or result[0] != "ok":
        raise AppError("Local state database failed its integrity check; restore a backup")


def backup_database(connection, path):
    directory = Path(path).parent / "db-backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / "state-v{}-{}.db".format(schema_version(connection), stamp)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    copy = sqlite3.connect(target)
    try:
        connection.backup(copy)
    finally:
        copy.close()
    return target


def migrate(connection, path=None):
    """Bring the database to CURRENT_VERSION. Returns the backup path or None."""
    version = schema_version(connection)
    if version > CURRENT_VERSION:
        raise AppError(
            "State database is from a newer Arch Games (schema {}); refusing to downgrade".format(version)
        )
    if version == CURRENT_VERSION:
        return None
    validate(connection)
    backup = None
    populated = connection.execute("SELECT 1 FROM records LIMIT 1").fetchone()
    if populated and path is not None:
        backup = backup_database(connection, path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        for target in range(version + 1, CURRENT_VERSION + 1):
            MIGRATIONS[target](connection)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta VALUES ('version', ?)", (str(CURRENT_VERSION),)
        )
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta VALUES ('migrated_at', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    validate(connection)
    return backup
