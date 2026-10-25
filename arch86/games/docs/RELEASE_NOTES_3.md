# Arch Games 3.0 — release notes

Branch `feature/arch-games-3` (based on `feature/arch-games-2` a5537c5). Not merged to `main`. **Fixture-verified; real-machine acceptance tests are listed in TESTING_3.md.**

## Highlights
* `arch-games launch undertale`: friendly names, aliases, ambiguity handling; discovery ranks the real executable (GOG play-task metadata, registry install locations, assets, names, PE headers;
  uninstallers/installers/crash reporters/updaters/redistributables excluded) and the confirmed launcher is stored with its evidence, prefix, runner, working directory, arguments and environment.
* `arch-games install NAME`: one resumable workflow (reuse or download → verify → safe inspection → plan → approval → extract → prefix → installer → discover → register → optional supervised test).
  Running a downloaded installer needs its own approval; the architecture of the installer never decides the prefix.
* Wine: layout/runtime awareness, doctor/status/logs with redaction, per-game settings, prefix backups/restore/export, 2.0 recovery fix preserved.
* Audio: layered diagnosis (device vs server vs sink vs Wine driver vs session), VM awareness with printed host advice, bounded tone test. Optional per-VM browser audio via a small host bridge
  (dedicated sink monitor, token auth, WebSocket + PCM + Web Audio), off by default.
* Interfaces: curses TUI (ASCII/no-colour/small-terminal fallbacks), optional web GUI (loopback, session+CSRF, allowlisted operations).
* Compatibility: separate evidence facts, per-machine records (arch86/arch64/cachyos), seven-state classification with reasons; only your confirmation on this machine verifies.
* Library: history, playtime estimates, optional CPU/memory monitoring, explicit benchmarks (FPS only from real mechanisms), favorites, tags, notes, titles, search, explainable recommendations, sync bundles.
* Saves: discovery without following symlinks, hash-verified backups, restore preview + safety backup. Downloads: priorities, pause/resume, archive reuse, dedupe.
* Data: versioned migrations with automatic backup; 2.0 data untouched.

## Behaviour changes
See MIGRATION_2_TO_3.md (`install --archive` semantics, `download add` reuse, `web`, `doctor`, optional `launch EXECUTABLE`).

## Security review
docs/SECURITY.md has the 3.0 review: 5 findings fixed (token file sharing with GameVault credentials, manifest-directed restore, bridge config permissions, CSP/inline style, doctor cost).

## Known limitations
* Python 3.10 not run locally; real Wine/Undertale/VM audio/Safari/Tailscale/noVNC not verified.
* Shortcut files (`.lnk`, `.desktop`) are not parsed as discovery evidence. Registry-stored saves are not backed up.
* FPS needs MangoHud or a Mesa HUD path and is otherwise "unavailable"; time-to-first-window needs `xdotool` on X11.
* The audio bridge streams uncompressed PCM (≈1.5 Mbit/s at 48 kHz stereo); no Opus/WebRTC; plain HTTP is for private tunnels only.
* The web GUI cannot install from local archives, register arbitrary executables or run arbitrary commands (by design).
* No automatic cross-machine sync; `sync export/import` is manual and file-based.

## Ideas for 3.1
Opus or WebRTC transport for low-bandwidth remote audio; `.lnk`/`.desktop` evidence; per-game save profiles from a community-free local rules file; Proton runtime helper with explicit user-supplied path;
persistent web job log; a native desktop front end over `core/gameview.py`; scheduled non-interactive health report; Python 3.10 CI matrix on the VMs.
