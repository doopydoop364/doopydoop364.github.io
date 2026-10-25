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
- [x] P5 Wine: doctor/status/logs, per-game settings, runtime abstraction, prefix backup/restore/export (fixture-verified)
- [x] P6 Audio doctor/devices/test
- [x] P7 Remote audio architecture decision + prototype (host bridge)
- [~] P8 Remote audio integration, auth, isolation, browser page (automated + synthetic real-browser verified; real VM/Safari/Tailscale NOT)
- [x] P9 TUI redesign (curses; pty-tested)
- [x] P10 Optional web GUI + API
- [x] P11 Compatibility evidence model, machine profiles
- [x] P12 History, playtime, performance, benchmark (FPS only via real mechanisms, never verified on hardware)
- [x] P13 Saves and backups
- [x] P14 Multi-machine, favorites, tags, notes, recommendations, search (+ download priorities/pause/dedupe)
- [x] P15 Hardening / security review
- [x] P16 Integration validation
- [x] P17 Docs and release prep (all 14 documents in the spec)
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

### Phase 5 (done; 158 tests pass)
`runtime/winetools.py` (runtime discovery + layout classification wow64/multilib/win32-only/win64-only from library
dirs, `wine doctor/runtimes/register-runtime/status/logs/config/apply-version`, per-game settings in namespace
`game_settings` merged into launch plans), `diagnostics/redact.py`, `PrefixManager.backups/restore/export_metadata`,
`prefix inspect/backups/restore/export`. Prefix recovery code untouched. Restore keeps the replaced prefix as a backup
and rolls back if the restored prefix fails its health check. Tests: test_wine_tools (14).
Limits: layout detection is evidence from file layout, not an execution probe; `apply-version` runs `winecfg -v` and is
unverified without real Wine; missing-DLL detection is a heuristic.

### Phase 6 (done; 172 tests pass)
`audio/probe.py` (read-only probes with injectable root/runner), `audio/diagnose.py` (layers: guest-device,
guest-audio-server, output-sink, wine-driver, session-environment; verdicts working / muted / no-audio-device /
device-without-server / server-without-sinks / server-only), `audio/tone.py` (bounded <=10 s sine, paplay/pw-play/aplay,
unprivileged). CLI `audio doctor [--json] | devices | test`. QEMU advice is printed only; nothing on the host is changed.
Real check: `audio doctor` and `audio test` were run on this CachyOS development host (verdict working; the test played a
0.3 s tone through PipeWire). Not verified: the Arch64/Arch86 guests themselves.

### Phases 7-8 (done except real-machine checks; 198 tests pass)
`arch_games/remote/` (ws.py, config.py, stream.py, server.py, __main__.py, static/player.{html,js,css}, wrapper.html) is the host
bridge: WebSocket + raw PCM + Web Audio, per-VM null-sink monitor capture, token + origin + throttling. `host-tools/audio-bridge/`
(README, make-pyz.sh). Guest side: `audio/remote.py` + `audio remote configure|status|url|instructions|disable`. Docs: REMOTE_AUDIO_3.md, AUDIO_3.md.
Tests: tests/test_remote_audio.py (26). Real browser run: see REMOTE_AUDIO_3.md "What has and has not been verified".
Incident: that browser test and `audio test` made audible tones on the dev host without warning the user; do not repeat without asking.
Remaining for P8: web GUI embedding of the audio dashboard (P10), install.sh must ship `remote/static` (P17).

### Phase 9 (done; 211 tests pass)
`ui/tui/{model,render,data,app}.py`: pure state machine + pure renderer + provider over the shared services + curses runner.
Sections Library/Installed/GameVault/Downloads/Compatibility/Wine/Audio/Settings/Diagnostics; search, preview pane (>=96 cols), detail view,
help overlay, per-row action keys that run the same CLI code path; ASCII fallback (TERM=linux, non-UTF-8, `menu --ascii`), NO_COLOR, small-terminal
degradation, plain menu fallback when no tty. Also added: `diagnostics/doctor.py` (shared by `doctor`, TUI, web), `diagnostics/repair.py`
(`repair GAME [--dry-run|--apply]`), `favorite add|remove|toggle|list`. Tests: tests/test_tui.py (13), including real curses sessions over a pty with no
DISPLAY. Not verified: KMSCON itself, colour rendering by eye on a real console.

### Phase 10 (done; 226 tests pass)
`ui/web/{api,server}.py` + `static/{index.html,app.css,app.js}`; `core/gameview.py` (one structured game description shared by TUI and web);
`web` command replaced by the new GUI (old read-only `ui/gallery.py` kept, no longer wired to the CLI). Library now also lists launcher-registered games.
Tests: tests/test_web.py (15). Real-browser pass found two bugs (DOM helper arrays, CSP inline style) - fixed. Docs: WEB_GUI_3.md.
Known gap found in the browser: Undertale shows compatibility "Unknown" because the registered launcher's executable evidence is not fed to the analyzer (Phase 11).

### Phase 11 (done; 237 tests pass)
`compatibility/intelligence.py` (machine identity, per-machine records in namespace `compat`, facts, classification), Library now measures the registered launcher's
executable and attaches `classification`, `classification_reasons`, `facts`, `machine` to every report; hooks on launch/install/feedback; CLI `compat show|list|set|export|import`,
`machine show|name`; TUI/web show the new classification. Tests: tests/test_compat_intelligence.py (11). The 2.0 `status` field is unchanged for compatibility.
Test change: bulk query bound raised 8 -> 12 (still constant for 2000 games).

### Phase 12 (done; 253 tests pass)
`core/history.py` (sessions, playtime estimate rules, recency), `performance/{sampler,fps,service}.py`, launcher `monitor=` hook, CLI `history [GAME]`,
`playtime`, `performance GAME` (reads only), `benchmark GAME --seconds N --yes` (explicit only), `launch --monitor`, setting `performance_monitoring`, `title set|clear`,
`notes`, `tag add|remove|list`; library applies custom titles (resolver matches them). Sampler reads /proc for the process tree (CPU as percent of one core, RSS, lifetime,
optional first-window time via xdotool); FPS comes only from MangoHud logs or Mesa Gallium HUD dumps and otherwise is reported unavailable. Tests: tests/test_performance_history.py (16,
including a real busy-loop process sampled via /proc). NOT verified: MangoHud/Gallium HUD with a real game, xdotool window detection, Wine process-tree sampling.

### Phase 13 (done; 266 tests pass)
`saves/service.py` + CLI `saves discover|paths|add|remove|backup|list|show|verify|restore`. Discovery looks in Wine prefix user profiles, conventional Linux data dirs and the game folder, never
follows symlinks (Wine links Documents to the real home), registers nothing automatically; manual folders are validated (no `/`, home, system dirs, symlinks). Backups are hash-verified, atomic (staging
dir + rename), 0700, size/file-count bounded; restore previews first, needs `--apply --yes`, refuses damaged backups/symlink targets, makes a `pre-restore` safety backup, never deletes. Hooks make a save backup
before `install --reinstall`, `prefix restore --apply` and `prefix remove` (when save folders are registered; otherwise it says nothing could be protected). Tests: tests/test_saves.py (13).
Limits: registry-stored saves are not covered; no cross-machine sync (by design in 3.0).

### Phase 14 (done; 279 tests pass)
`catalog/search.py` (indexed search, 20k-entry timing test), `recommend.py` (explainable; compatibility reported separately; blocked games never suggested), `sync.py` (file-based bundle: favorites, aliases, notes, tags,
titles, interests, own compat records, install metadata without paths; last-writer-wins by row timestamp; alias repoint needs --prefer-remote), CLI `search`, `recommend`, `interests`, `sync export|import`.
Download management (spec part 15): priorities (`download add --priority`, `download priority`), `download pause` (keeps the partial; retry resumes), completed-archive reuse (`add` returns the existing job unless `--force`;
behaviour change vs 2.0 — one legacy test now passes `force=True`), `download dedupe [--apply]` (hard-links identical archives, deletes nothing). Web: pause button. Tests: tests/test_library_features.py (8), tests/test_downloads.py (+5).

### Phase 15 (done; 287 tests pass on Python 3.14.8 and 3.13.14)
Security review recorded in docs/SECURITY.md ("Arch Games 3.0 security review"), 5 findings fixed. New: tests/test_failure_recovery.py (6: corrupt DB untouched, Ctrl-C during installer,
missing Wine during prefix creation, corrupt archive, killed download resumed, failed launch status), tools/bench_startup.py. Performance: `doctor` was 689 ms vs 85 ms in 2.0 because of
graphics probes; quick `doctor` now skips them (161 ms; `--verbose` keeps them); `--version` 68 -> 81 ms, `list --source pacman` 845 -> 876 ms (median of 5, this CachyOS host, Python 3.14.8).
Python 3.10: no interpreter available locally; static check (`ast.parse(feature_version=(3,10))` + grep for 3.11+ APIs) is clean but 3.10 has NOT been run.

### Phase 16 (done; 289 tests pass)
tests/test_e2e_undertale.py: loopback GameVault -> `gamevault list` -> alias -> `install undertale` (one download, verification record, plan, installer NOT run) -> `--approve-installer`
(fake wine creates a GOG layout in the real managed prefix) -> discovery -> registration -> `launch undertale` (WINEPREFIX/cwd checked) -> second `install` is a no-op (no re-download) -> compat classification
(Likely compatible -> Verified working only after `compat set`) -> DB integrity. The E2E found a real bug (download verification label treated as a dict) - fixed.
tools/audio_bridge_selftest.py: serves a synthetic tone so the user can check browser/network/token on their own machines. Browser audio harness automated pieces: tests/test_remote_audio.py; real-browser run
described in REMOTE_AUDIO_3.md. Fake Wine only records calls: this is NOT a Wine or gameplay test.

### Phase 17 (done; 296 tests pass)
Version 3.0.0 (package, pyproject, installer smoke test); installer ships docs/host-tools/tools and accepts 2.0 launchers; wheel built offline and checked for static assets; tests for installed layout and 2.0 upgrade.
Docs written: README, ARCHITECTURE_3, INSTALLATION_3, CLI_3 (generated reference + sync test), WINE_3, AUDIO_3, REMOTE_AUDIO_3, WEB_GUI_3, COMPATIBILITY_3, MIGRATION_2_TO_3, TESTING_3 (incl. real-machine plan),
TROUBLESHOOTING_3, RELEASE_NOTES_3, DEVELOPMENT_PROGRESS_3 (this file), SECURITY.md 3.0 section; 2.0 docs bannered.
