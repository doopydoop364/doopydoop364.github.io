# Migrating from Arch Games 2.0

**Nothing is wiped and nothing needs to be re-entered.** GameVault settings and credentials, downloaded archives, extracted games, Wine prefixes (including `gamevault:32`), launch history,
compatibility evidence and feedback stay where they are and keep working.

## What happens on upgrade
1. `install.sh` replaces the application copy (the previous one is kept as `app.previous`); the launcher recognises 2.0's launcher and keeps your aliases.
2. On the first run the database `data/state.db` is validated (`PRAGMA quick_check`), a consistent copy is written to `data/db-backups/state-v1-<UTC time>.db` (mode 0600), and schema version 2 is applied
   in a single transaction (adds `schema_meta` and an index; no 2.0 table or namespace is rewritten). A failure rolls back and leaves the file untouched. A newer schema than this program knows is refused.
3. New namespaces are created lazily (`aliases, launchers, install_jobs, compat, …`).

## Behaviour changes to know about
| 2.0 | 3.0 |
|---|---|
| `launch GAME EXECUTABLE` | still works; `EXECUTABLE` is now optional (registered launcher, or guided discovery) |
| `install ID --archive F` extracted only | plans a full install; the old behaviour is `--extract-only`. `--yes` still never runs an installer (`--approve-installer` does) |
| `download add` of an already-downloaded game queued another copy | returns the stored archive; `--force` downloads again |
| `web` was a read-only gallery | `web` is the new GUI with login and allowlisted actions; the old module remains in the tree but is not wired to the CLI |
| `doctor` printed raw JSON | human report; `--json` for scripts; exit status 1 if a check fails |
| compatibility label only | `status` unchanged, plus `classification` and `facts` in reports |
| Library hid games without records | games with a registered launcher appear even without a 2.0 install record |

## IDs: bare vs versioned
2.0 let you create a prefix under `gamevault:32` while the library lists the game as `gamevault:32@34`. 3.0 treats them as one game for local state: your existing `gamevault:32` prefix is found when you
say `undertale`, `discover`/`launch`/`install` do not create a second prefix or download again, and the library shows the game as installed. If one bare ID has several versioned records, 3.0 never guesses between them.

## Rollback
Stop 3.0, copy the backup over `state.db`, reinstall the 2.0 ref (`--ref feature/arch-games-2`). 2.0 ignores the 3.0 namespaces. Data written only by 3.0 (aliases, launchers, compat records, save backups)
stays in the files and database copy you restore from, so keep the backup you are rolling back *from* if you may return to 3.0.

## Verify after upgrading
`arch-games doctor`; `arch-games prefix list`; `arch-games download list`; `arch-games installed`; `ls ~/.local/share/arch-games/data/db-backups`.
