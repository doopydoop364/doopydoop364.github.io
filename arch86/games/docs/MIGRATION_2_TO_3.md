# Migrating from Arch Games 2.0

The 2.0 database (`state.db`, table `records`) is retained unchanged. 3.0 adds a `schema_meta` table and stamps
version 2. Before the first migration on a non-empty database a consistent copy is written to
`data/backups/state-v1-<timestamp>.db` (SQLite online backup). Migrations run in one transaction and are idempotent.
New data lives in new namespaces (`aliases`, `launchers`, ...); no 2.0 namespace is rewritten.
Rollback: stop 3.0, copy the backup over `state.db`, reinstall the 2.0 ref.
