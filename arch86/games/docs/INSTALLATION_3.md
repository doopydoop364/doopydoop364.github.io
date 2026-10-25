# Installation and update (3.0)

Run as your ordinary user, never root. Nothing below installs Wine, drivers or system packages for you; the commands are yours to run.

## Application
```sh
# from a reviewed checkout (recommended)
git clone -b feature/arch-games-3 https://github.com/doopydoop364/doopydoop364.github.io.git
sh doopydoop364.github.io/arch86/games/install.sh

# or the bootstrap (executes the installer you request; pin a reviewed commit with --ref SHA)
curl -fsSL https://raw.githubusercontent.com/doopydoop364/doopydoop364.github.io/feature/arch-games-3/arch86/games/install.sh | sh
```
Upgrading from 2.0 uses the same command: settings, credentials, downloads, prefixes, games and the database are kept; the previous app version stays in `app.previous`.
The database migrates on the first run, after an online backup in `~/.local/share/arch-games/data/db-backups/` (details in MIGRATION_2_TO_3.md). Then run `arch-games doctor`.
Aliases `arch-games`, `arch86-games`, `arch64-games` are installed in `~/.local/bin` (Fish: `fish_add_path ~/.local/bin`). Remove with `sh ~/.local/share/arch-games/app/uninstall.sh` (user data is retained).

## Packages you may want (explicit, per machine)
| Purpose | Arch64 (x86_64) | Arch86 (Arch Linux 32) |
|---|---|---|
| Python ≥ 3.10, curses | `python` | `python` |
| Archive extraction | `libarchive` | `libarchive` |
| Wine | `wine` (modern WoW64 builds create win64 prefixes that also run 32-bit games), optionally `wine-mono wine-gecko` | `wine` as available for i686; only win32 prefixes are possible on a 32-bit OS |
| Audio | `alsa-utils libpulse pipewire-pulse wireplumber` | same |
| Graphics diagnostics | `mesa-utils vulkan-tools` | `mesa-utils` |
| Optional measurement | `mangohud` (frame rate), `xdotool` (time to first window) | same if available |

Check the result, do not assume it: `arch-games wine doctor` (build layout, prefix architectures, display, renderer), `arch-games audio doctor`, `arch-games doctor --verbose`.

### Arch64 VM (4 vCPU / 4 GB, QEMU llvmpipe, Intel HDA → host PipeWire)
1. Install Wine and the audio tools above; log in through a real session (i3) so `XDG_RUNTIME_DIR` exists; start `pipewire pipewire-pulse wireplumber` for your user if they are not running.
2. `arch-games wine doctor` should report a `wow64` or `multilib` layout and prefix architecture win64 available.
3. `arch-games audio doctor` should say `working` (you reported local audio already works).
4. GameVault: `arch-games gamevault connect --url https://your-server` (credentials are prompted, never stored in the repository).

### Arch86 VM (2 vCPU / 2 GB, 32-bit)
1. Same tools; Wine capability may differ: only `win32` prefixes can be created and a PE32+ game cannot run. The analyzer and `install` plan say so instead of assuming.
2. Expect heavier games to be rated Likely incompatible by memory/graphics evidence. `doctor` is light (≈0.16 s); avoid `doctor --verbose` while a game runs.

### Optional components
* Web GUI: nothing to install: `arch-games web` (WEB_GUI_3.md).
* Browser audio: runs on the VM *host* (REMOTE_AUDIO_3.md): `host-tools/audio-bridge/make-pyz.sh` builds one file to copy there.

## Verifying an installation
`arch-games --version` → `3.0.0`; `arch-games doctor` shows "All installed file hashes match" for the installed copy; `python3 -m unittest discover -s tests` from a checkout runs the automated suite (TESTING_3.md).
