# Arch Games 3.0 architecture

Stdlib-only Python 3.10+. Optional pieces (web GUI, remote audio bridge, performance sampling, Wine helpers) are separate modules that nothing else imports at startup.

```
 terminal (curses TUI / plain menu)     browser                      host (CachyOS)
        ui/tui/*                        ui/web/{server,api}.py       arch_games/remote  (python -m arch_games.remote)
              \                         /   static/*                  parec/pw-record on <vm sink>.monitor
               +--> cli.py (dispatch) --+                                     |  WebSocket + PCM
                         |                                                    v
   shared services: identity/ catalog/ compatibility/ runtime/ installation/ downloads/ saves/ audio/ performance/ core/ diagnostics/
                         |
   storage/ (SQLite key-value records + versioned migrations)     config.py (XDG dirs, 0700/0600, atomic JSON)
                         |
   adapters: gamevault/client.py · catalog/pacman.py · runtime/prefixes.py + winetools.py (Wine) · audio/probe.py (ALSA/Pulse/PipeWire tools)
```

## Layers and what lives where
| Package / file | Responsibility |
|---|---|
| `cli.py` | argument parsing and thin command handlers; resolves game names once, then calls services. No business rules that the other front ends would need to copy. |
| `ui/tui/model.py, render.py, data.py, app.py` | pure state machine, pure renderer (width/height/caps → spans), provider over services, curses glue. Actions run the same CLI code path. |
| `ui/web/api.py, server.py, static/` | allowlisted JSON operations over the same services; session+CSRF auth; vanilla JS. |
| `core/gameview.py, history.py` | one structured game description (used by TUI and web) and launch history/playtime rules. |
| `identity/` | resolver (IDs, aliases, titles, fuzzy, ambiguity) and the per-command service. |
| `catalog/library.py, search.py` | combine Pacman, GameVault cache and local records; attach compatibility reports; indexed search. |
| `compatibility/` | 2.0 analyzer + archive/PE inspection (unchanged) and `intelligence.py` (facts, per-machine records, classification). |
| `runtime/discovery.py, launchers.py, launcher.py` | find the real game executable, persist the user-confirmed launcher, launch it (argv arrays only). |
| `runtime/prefixes.py, winetools.py` | prefix lifecycle (2.0 recovery logic intact), runtimes/layouts, doctor/status, per-game settings. |
| `installation/workflow.py` | resumable install job: acquire → verify → inspect → plan → approve → extract → prefix → installer → discover → register → test. |
| `downloads/` | persistent queue with priority/pause/resume/dedupe. |
| `saves/` | save discovery, hash-verified backups, safe restore. |
| `audio/` | read-only probes, layered diagnosis, bounded tone test, guest-side bridge client. |
| `remote/` | the host-side audio bridge (self-contained so it can be shipped as one `.pyz`). |
| `performance/` | /proc sampler, real-only FPS mechanisms, explicit benchmark. |
| `diagnostics/` | `doctor` (shared by CLI/TUI/web), `repair`, log redaction. |
| `storage/` | `records(namespace,key,value,updated)` plus `schema_meta`; migrations back up first and run in one transaction. |

## Data model
Existing 2.0 namespaces are untouched (`installs, prefixes, launches, analysis, feedback, downloads, library, catalog_identity, events, install_transactions`).
3.0 adds: `aliases, launchers, install_jobs, game_settings, compat, favorites, tags, notes, custom_titles, interests, save_paths, performance`.
All timestamps are ISO-8601 UTC with an explicit offset.

## Key boundaries
* Browser → process: only `launch GAME_ID` (registered launcher) and `install` of GameVault game IDs after hash-bound approval. No argv/paths/env from the browser.
* Guest ↔ host: the guest never touches the host's PipeWire. The host bridge captures one dedicated sink monitor per VM; the guest stores only the bridge URL and a token.
* Installer vs game: installer architecture and installed-game architecture are separate facts everywhere (plan, facts, classification). A PE32 game still gets a win64 (WoW64) prefix on x86_64.
* Evidence vs claims: compatibility is derived from facts with reasons; only the user's own confirmation on this machine verifies gameplay.

Changes needed for a future desktop GUI: none in the core; it would be a fourth front end over the same services and `core/gameview.py`.
