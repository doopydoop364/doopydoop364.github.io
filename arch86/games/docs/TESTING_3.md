# Testing (3.0)

## Automated (what has been verified, and what that means)
```sh
cd arch86/games
python3 -m unittest discover -s tests            # full suite (about 100 s; the E2E test waits on absent X11 tools)
python3 -m unittest discover -s tests -p 'test_identity.py'   # one area
python3 -m compileall -q arch_games tests tools
python3 tools/gen_cli_reference.py --write       # refresh docs/CLI_3.md (a test fails when it is stale)
```
Verified with this suite: 321 tests pass on Python 3.14.8 and 3.13.14 locally (with and without a graphical session) and on Python 3.10 and 3.14 in the repository's GitHub Actions
workflow (`.github/workflows/arch-games.yml`: libarchive check, `ruff check`, the suite, compileall, `sh -n`). CI is Ubuntu: it says nothing about Arch, Wine, audio or your VMs.

| Area | Test modules |
|---|---|
| 2.0 behaviour (regression) | the original 103 tests, unchanged except 3 deliberate semantic updates (`--extract-only`, download archive reuse needs `force`, query bound 8→12) |
| Migrations | test_migrations (2.0 fixture, idempotent, rollback, newer schema refused) |
| Names/aliases | test_identity, test_version_ids (the real 2.0 state: prefix keyed `gamevault:32`, library listing `gamevault:32@34`) |
| Discovery/launchers | test_discovery_launch (Undertale-shaped GOG layout, symlinks, uninstaller exclusion, launch by name with a fake wine) |
| Install workflow | test_install_workflow, test_e2e_undertale (loopback GameVault → launch by name), test_failure_recovery |
| Wine/runtime | test_wine_tools, test_prefix_recovery (2.0) |
| Audio | test_audio (layers, VM guest without device, server down, muted), test_remote_audio (real sockets, auth, lifecycle, isolation) |
| Interfaces | test_tui (state machine, renderer at every size, real curses over a pty with no DISPLAY), test_web (real HTTP server: sessions, CSRF, no exec surface) |
| Compatibility/history/saves/library | test_compat_intelligence, test_performance_history, test_saves, test_library_features, test_downloads |
| Packaging | test_app_install (installed copy ships static assets, 2.0 launcher upgrade) |

Real, non-simulated automated checks: curses runs in a pseudo-terminal; sockets and subprocess capture in the bridge tests; a busy-loop process sampled through the real `/proc`; the real HTTP server.
Simulated: Wine, wineboot, wineserver (scripts that record calls), GameVault (loopback fixture), PipeWire (stub tools). **No test proves that a game runs or that sound is audible.**

Performance reference (tools/bench_startup.py, median of 5, fresh state dir, this host): `--version` 68→81 ms, `list --source pacman` 845→876 ms, `doctor` 85→161 ms (2.0→3.0).
Search over 20,000 games: index build < 3 s, a query < 0.35 s (asserted in tests; typically much faster).

## Manual real-browser checks already performed (dev host, in-app Chromium pane)
Remote audio page with a synthetic tone (`tools/audio_bridge_selftest.py` style): connect, AudioContext running, frames received, buffer ≈130 ms, server returned to idle on disconnect and no capture process was left.
Web GUI: sign-in from the URL fragment, library, game detail, downloads progress bar, audio/wine/diagnostics views. These found and fixed a DOM-helper bug and a CSP/inline-style bug.

## Real-machine test plan (NOT yet performed: needs the user's VMs/host)
Do these in order; stop and report at the first surprise. Nothing here is destructive; nothing modifies host audio unless you choose to run the printed commands.

**Arch64 VM**
1. `sh install.sh`; `arch-games --version` = 3.0.0; `arch-games doctor`; upgrade check: GameVault settings and `arch-games prefix list` still show your data; `ls ~/.local/share/arch-games/data/db-backups`.
2. Test C: `arch-games prefix check gamevault:32` → healthy, state ready (the Undertale prefix is not modified).
3. Test A: `arch-games discover undertale` → `UNDERTALE.exe` ranked first (`unins000.exe` absent); `arch-games discover undertale --register 1`; `arch-games launch undertale --dry-run` (check WINEPREFIX/cwd) then `arch-games launch undertale` from the desktop session. Confirm the game starts without setting WINEPREFIX.
4. `arch-games compat show undertale` → Likely compatible; after playing: `arch-games compat set undertale --gameplay works --audio works` → Verified working (this machine only).
5. Test B: `arch-games install undertale` → "already_installed" (launcher registered), no download. For a fresh game: `install GAME` → review plan → `--yes --approve-installer`.
6. Test D: `arch-games audio doctor` → working; on a VM clone without the HDA device it must say no-audio-device (not "server down").
7. `arch-games wine doctor`, `wine status undertale`, `wine logs undertale` (check redaction), `performance undertale` after `launch --monitor`.
8. Test F: `arch-games` over SSH/KMSCON (no DISPLAY) — browse, search `/`, `?`, quit. Check colours on KMSCON; try `--ascii`.
9. Test G: `arch-games web` inside the VM; open the printed URL (loopback or via your VNC browser); log in; browse; Launch needs the desktop session.
10. Saves: `saves discover undertale` (expect `…/AppData/Local/UNDERTALE`), `saves add`, `saves backup`, change a save, `saves restore … ` (preview), then `--apply --yes`; confirm the safety backup exists.

**CachyOS host (remote audio, Test E)**
1. Build/copy the bridge (`host-tools/audio-bridge/make-pyz.sh`), `init --vm arch64=arch64_vm`, `sink create arch64`, `qemu arch64` and add the printed flags to the VM yourself (output-only HDA), restart the VM.
2. `python arch-games-audio-bridge.pyz doctor`, then `serve`; open `http://127.0.0.1:8787/vm/arch64/#token=…` locally, click Enable audio, play Undertale in the VM: audio only from that VM; start a host sound (it must not appear in the stream).
3. Start Arch86 with its own sink: confirm isolation (each page hears only its VM).
4. Remote: `serve --listen <tailscale-ip> --allow-nonloopback`; from the MacBook (Safari/Chrome) open the page, enable audio, listen for dropouts; note the buffer figure. Check Disconnect stops capture (`api/status` shows idle).
5. Optional wrapper page with noVNC (`novnc_url` in `bridge.json`): keyboard/mouse still work in the iframe.
6. Guest: `arch-games audio remote configure --url http://<host>:8787 --vm arch64 --allow-http`, `audio remote status`.

**Arch86 VM:** repeat 1–3 and 8 with a game that fits 2 GB; record what `wine doctor` says about win32/PE32+; confirm a PE32+ game is rated Likely incompatible rather than failing mysteriously.
