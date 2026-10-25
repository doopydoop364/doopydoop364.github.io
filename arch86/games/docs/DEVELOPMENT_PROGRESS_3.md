# Arch Games 3.0 development progress

Branch: `feature/arch-games-3` (based on `feature/arch-games-2` @ a5537c5). Never merged to `main`.
Baseline (Phase 0): 103 tests pass, Python 3.14.8 (`python3 -m unittest discover -s tests`).
This file is the resume point for a future session: update it after every phase.

## Verification honesty

Everything below is **automated fixture verification** unless a line says otherwise. The development host
(CachyOS, no Wine installed) has no access to the Arch64/Arch86 VMs, the private GameVault, PipeWire
routing or noVNC. Real Undertale launching, real Wine runs and real browser audio are **not** verified here;
see `VALIDATION.md` and the real-VM checklist in `TESTING_3.md` once written.

## Requirement checklist (status: [ ] todo, [~] partial, [x] done+tested)

- [x] P0 Discovery, baseline, architecture and migration plan
- [~] P1 Versioned DB migrations done (backup, idempotent, rollback, 2.0 fixture); full CLI refactor into services deferred to when TUI/web need it (P9/P10)
- [x] P2 Friendly names, aliases, ambiguity handling across commands
- [x] P3 Executable discovery, classification, launcher registration, `launch NAME` (fixture-verified; real Undertale launch NOT verified)
- [x] P4 One-command `install` workflow with persisted, resumable job state (fixture-verified; no real installer run)
- [ ] P5 Wine: doctor/status/logs, per-game settings, runtime abstraction, prefix backup/restore/export
- [ ] P6 Audio doctor/devices/test
- [ ] P7 Remote audio architecture decision + prototype (host bridge)
- [ ] P8 Remote audio integration, auth, isolation, browser page
- [ ] P9 TUI redesign
- [ ] P10 Optional web GUI + API
- [ ] P11 Compatibility evidence model, machine profiles
- [ ] P12 History, playtime, performance, benchmark
- [ ] P13 Saves and backups
- [ ] P14 Multi-machine, favorites, tags, notes, recommendations, search
- [ ] P15 Hardening / security review
- [ ] P16 Integration validation
- [ ] P17 Docs and release prep (all 14 documents in the spec)
- [ ] P18 Requirement-by-requirement audit

## Phase log

### Phase 0 — discovery (done)
Findings: ~6.3k lines, stdlib only. State is a generic SQLite key/value table `records(namespace,key,value,updated)`
(namespaces: installs, prefixes, launches, analysis, feedback, downloads, library, catalog_identity, events,
install_transactions). Game IDs: `pacman:*`, `local:*`, `gamevault:ID[@VERSION]`. Prefixes are keyed by game ID.
`launch GAME EXECUTABLE` requires an explicit path inside a managed install dir (or `--allow-external`).
Weaknesses: no schema version, no name resolution (`Library.find` is exact ID/package), launcher has no persisted
executable, `dispatch()` in cli.py is one 300-line function, Wine launch needs a manifest root.
Strategy: keep the KV store (existing data stays valid), add a `schema_meta` table and numbered migrations,
add new namespaces rather than altering old ones, add `identity/`, `runtime/discovery.py`, `runtime/launchers.py`.

### Phases 1-3 (done, tests: 127 pass, Python 3.14.8; 3.10 not available on this host)
Files: `storage/migrations.py`, `storage/database.py` (schema v2, backup in `data/db-backups/`),
`identity/resolver.py`, `identity/service.py`, `runtime/discovery.py`, `runtime/launchers.py`,
`runtime/launcher.py` (`plan_registered`, `extra_roots`, `cwd`), `cli.py` (`alias`, `discover`, `launcher`,
`info GAME`, `settings game GAME`, `launch GAME [EXE]`, `analyze GAME`, `--non-interactive`).
Tests added: test_migrations (5), test_identity (9), test_discovery_launch (10).
Behaviour: every game-taking command resolves names via the resolver (ID -> alias -> title -> casefold -> normalized
-> unique fuzzy; ambiguity is an error listing IDs, or an interactive picker on a TTY). `launch NAME` uses the
registered launcher; with none, discovery runs and the user must confirm (non-interactive refuses to guess).
Known issues / remaining: .desktop/.lnk shortcut evidence is not parsed (documented limitation); fuzzy matching is
announced on stderr. Python 3.10 compatibility untested locally (no interpreter) - CI/VM check needed.

### Phase 4 (done; 142 -> 144 tests pass)
`installation/workflow.py` (steps locate, acquire, verify, inspect, plan, approve, extract, prefix, installer, discover,
register, test, finish; job in namespace `install_jobs`). CLI: `install GAME [--archive F | --directory D] [--yes]
[--approve-installer] [--plan-only] [--installer MEMBER] [--silent-inno] [--test-launch] [--abort] [--status]
[--extract-only]`. Safety: `--yes` approves extraction only; running the downloaded installer needs
`--approve-installer` (or an interactive yes). Existing completed downloads are reused. Behaviour change vs 2.0:
`install ID --archive F` now plans a full install; the 2.0 extract-only behaviour is `--extract-only`.
Executable discovery also detects extension-less executable ELF files. Launch records gain `purpose`.
Limits: GOG multi-part `.bin` handling relies on extraction placing parts next to the setup exe; Inno silent flags
are opt-in and only offered with an Inno Setup marker; no real Wine installer was executed.
