# Arch Games 2.0

A local-first game catalog, compatibility analyzer, download manager, archive
installer and launcher for **Arch Linux 32 (i686/Pentium4)** and **Arch Linux
x86-64**. Python 3.10+, no third-party Python runtime packages. Pacman browsing
works without Wine or GameVault. Standard terminal, KMSCON, SSH and tmux are
supported; the browser gallery binds only to localhost.

## Install

Run as your ordinary VM user on either target:

```sh
curl -fsSL https://raw.githubusercontent.com/doopydoop364/doopydoop364.github.io/feature/arch-games-2/arch86/games/install.sh | sh
```

This intentionally executes the application installer you requested. For a
reviewable local installation, clone the development branch, inspect the source,
and run `sh arch86/games/install.sh`. To pin a reviewed revision, use that SHA in
the raw URL and pass `sh -s -- --ref SHA`. Nothing installs Wine, graphics tools or
system packages automatically. The installer checks Pacman and Python, stages and
smoke-tests application files, migrates a legacy custom catalog and preserves
settings. Application upgrades retain the previous app version. All three aliases
work: `arch-games`, `arch86-games`, `arch64-games`.

```sh
arch-games doctor
arch-games                    # keyboard menu; --plain is available via menu
arch-games list --source pacman
arch-games web                # open http://127.0.0.1:8765 inside the VM
```

If necessary: Fish `fish_add_path ~/.local/bin`, or add `~/.local/bin` to your
shell's PATH. The local installation is under `${XDG_DATA_HOME:-~/.local/share}/arch-games/app`.
No pip downloads are needed. Optional archive operations require system
`libarchive`; other optional tools are reported by `arch-games doctor`.

## Connect and play

```sh
arch-games gamevault connect --url https://your-private-server.example
arch-games gamevault status
arch-games gamevault list
arch-games list --source gamevault --all
arch-games download add 'gamevault:12@34'
arch-games download run
arch-games download list
arch-games install 'gamevault:12@34' --archive '/path/from/download/list/Game.zip'
arch-games details 'gamevault:12@34' --offline
arch-games prefix create 'gamevault:12@34' --arch win64
arch-games launch 'gamevault:12@34' 'Game/Game.exe' --dry-run
arch-games launch 'gamevault:12@34' 'Game/Game.exe'
arch-games feedback set 'gamevault:12@34' works --notes 'Played for 20 minutes'
```

IDs with `@` select a specific server version. Check your actual IDs and paths;
these are examples, not assertions that the games exist. Installation only
extracts files. Windows setup programs still need an explicit user launch with
Wine; the app never runs them automatically. See the Wine guide before choosing
win32 versus win64, particularly on modern WoW64 installations.

## Compatibility is evidence, not a promise

**Verified Working** means a local user attested to success for the recorded
configuration. **Promising** is a reasoned, unverified prediction.
**Experimental** has significant uncertainty. **Unlikely** indicates resource or
performance concerns. **Unsupported** identifies a concrete blocker for the
selected method. **Unknown** lacks meaningful evidence. Architecture, runtime,
graphics, memory, dependencies, evidence and uncertainties are reported
separately. A successful process exit does not verify gameplay.

GameVault's public API has no executable architecture, system requirements,
checksums or remote archive inspection. Such data is never invented. Use
`analyze` on an already-local file, with an explicit member selection for archive
evidence. Unknown GameVault entries are hidden by default in the combined view;
`--all` exposes them with explanations. Pacman entries are filtered by this
machine's configured repositories, which establishes availability, not gameplay.

## Guides

- [Complete CLI reference](docs/CLI.md)
- [GameVault connection and offline use](docs/GAMEVAULT.md)
- [Wine and prefixes](docs/WINE.md)
- [Terminal and visual interfaces](docs/INTERFACES.md)
- [Compatibility methodology](docs/COMPATIBILITY.md)
- [Troubleshooting and target VMs](docs/TROUBLESHOOTING.md)
- [Security boundaries](docs/SECURITY.md) and [archive limits](docs/ARCHIVE_SECURITY.md)
- [Architecture and baseline audit](docs/ARCHITECTURE.md)
- [API contract and version uncertainties](docs/GAMEVAULT_API.md)
- [Developer guide](docs/DEVELOPMENT.md) and [phase progress](docs/DEVELOPMENT_PROGRESS.md)
- [Integration validation and real-VM checklist](docs/VALIDATION.md)

## Remove

```sh
sh ~/.local/share/arch-games/app/uninstall.sh
```

With a custom XDG data location, use its corresponding app path. Removal deletes
application files and managed aliases while retaining credentials, configuration,
downloads, installed games, prefixes, backups and the old legacy installation.
Use `gamevault disconnect` first if you also want local tokens removed. Game
`uninstall ID` and `prefix remove ID` move files to backups; `--purge` requests
permanent deletion of those selected files. No server files or data are deleted.

## Validation status

Automated fixture and loopback tests are the primary evidence. No private server,
real Arch86/Arch64 VM, hardware graphics stack, real game or real Wine execution
has been verified during this development. The full 77-test suite passes on Python 3.10.20 and 3.14.8; a wheel installation and gallery interaction were checked separately. Read the validation matrix before
production use. The branch is for review and VM acceptance testing; no main
branch merge or production deployment is performed.
