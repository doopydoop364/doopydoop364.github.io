# Arch Games 3.0

A personal, local-first Linux game manager for **Arch Linux (x86-64)** and **Arch Linux 32 (i686)**: library, GameVault downloads, one-command Windows installs under Wine,
executable discovery, per-machine compatibility evidence, audio diagnostics with optional browser audio for VMs, saves and backups, an interactive terminal UI and an
optional web GUI. Python 3.10+, no third-party runtime packages. Terminal first: nothing needs a web server, a desktop session is needed only to *play* games.

> **Validation status (read this).** The automated suite (fixtures, loopback GameVault, fake Wine scripts, real sockets, real curses over a pty) passes on Python 3.14 and 3.13; Python 3.10 has not been run.
> No real Wine run, real game, VM, PipeWire routing, Tailscale link or Safari session has been verified by the developer. Undertale GOG 1.08 working on Arch64 under Wine 11.20 is *your* report; 3.0 is designed
> around it but has not been re-run there. See [TESTING_3.md](docs/TESTING_3.md) for the exact real-machine plan.

## Install / upgrade
```sh
git clone -b feature/arch-games-3 https://github.com/doopydoop364/doopydoop364.github.io.git
sh doopydoop364.github.io/arch86/games/install.sh
arch-games doctor
```
2.0 data is kept; the database is backed up and migrated on first run ([MIGRATION_2_TO_3.md](docs/MIGRATION_2_TO_3.md)). Nothing installs Wine or system packages for you ([INSTALLATION_3.md](docs/INSTALLATION_3.md)).

## Everyday use
```sh
arch-games                               # interactive manager (Library, Installed, GameVault, Downloads, Compatibility, Wine, Audio, Settings, Diagnostics)
arch-games alias add undertale gamevault:32
arch-games install undertale             # reuse/download, verify, inspect, plan; asks before anything runs
arch-games install undertale --yes --approve-installer
arch-games discover undertale --register 1   # confirm which .exe starts the game
arch-games launch undertale              # right prefix, right folder, no manual WINEPREFIX
arch-games compat set undertale --gameplay works --audio works   # your confirmation, this machine only
arch-games doctor                        # application, database, storage, Wine, audio, remote audio, web
arch-games web                           # optional GUI on 127.0.0.1:8765 (token login)
```
Names, IDs, aliases and fuzzy matches all work; ambiguity is never guessed. Full list: [CLI_3.md](docs/CLI_3.md).

## What is new in 3.0 (details in [RELEASE_NOTES_3.md](docs/RELEASE_NOTES_3.md))
Friendly names and aliases · executable discovery and persisted launchers · resumable guided installs with separate installer approval · Wine runtime/layout awareness, doctor/status/logs,
per-game settings, prefix backup/restore/export · layered audio diagnostics · optional per-VM browser audio ([REMOTE_AUDIO_3.md](docs/REMOTE_AUDIO_3.md)) · redesigned TUI and optional web GUI
([WEB_GUI_3.md](docs/WEB_GUI_3.md)) · evidence facts and per-machine classification ([COMPATIBILITY_3.md](docs/COMPATIBILITY_3.md)) · history, playtime, optional monitoring and explicit benchmarks ·
save discovery/backup/restore · favorites, tags, notes, search, explainable recommendations, private sync bundles · download priorities, pause/resume and dedupe · versioned database migrations.

## Guides
[Architecture](docs/ARCHITECTURE_3.md) · [Installation](docs/INSTALLATION_3.md) · [CLI](docs/CLI_3.md) · [Wine](docs/WINE_3.md) · [Audio](docs/AUDIO_3.md) · [Remote audio](docs/REMOTE_AUDIO_3.md) ·
[Web GUI](docs/WEB_GUI_3.md) · [Compatibility](docs/COMPATIBILITY_3.md) · [Migration](docs/MIGRATION_2_TO_3.md) · [Testing](docs/TESTING_3.md) · [Troubleshooting](docs/TROUBLESHOOTING_3.md) ·
[Security (incl. 3.0 review)](docs/SECURITY.md) · [Progress and requirement checklist](docs/DEVELOPMENT_PROGRESS_3.md).
2.0 references that still apply: [GameVault](docs/GAMEVAULT.md), [API notes](docs/GAMEVAULT_API.md), [archive limits](docs/ARCHIVE_SECURITY.md), [prefix recovery](docs/PREFIX_RECOVERY_FIX.md), [methodology](docs/COMPATIBILITY.md).

## Remove
`sh ~/.local/share/arch-games/app/uninstall.sh` removes the application and aliases; settings, credentials, library, downloads, prefixes, backups and saves are retained. `uninstall ID` and `prefix remove ID`
move files to backups; `--purge` deletes only the selected files. No server data is ever deleted.
