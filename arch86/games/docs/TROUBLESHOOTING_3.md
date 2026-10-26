# Troubleshooting (3.0)

Start with `arch-games doctor --verbose`; it names the failing layer and the fix. `arch-games repair GAME` plans safe repairs (`--apply` runs only the automatic ones).

| Symptom | Likely cause and fix |
|---|---|
| `No game matches 'x'` | Titles come from the cached library: `arch-games gamevault list` once, then retry; `arch-games search x`; define an alias. |
| `'x' matches several games; use one of these IDs` | Use the ID shown or an alias (`alias add`). Scripts must pass IDs. |
| `launch NAME` asks which executable | No launcher registered yet: pick from the ranked list or `discover NAME --register N`. Non-interactive runs refuse to guess. |
| `Registered executable no longer exists` | Game moved/uninstalled: `repair GAME`, then `discover GAME`. |
| Install stops at `awaiting_approval` | Intentional. Review the plan; `--yes` approves extraction, `--approve-installer` runs the installer. `install GAME --status` shows the job. |
| Install failed midway | Re-run the same command: it resumes (no re-download, no re-extraction). `--abort` cancels and moves extracted files to backups. |
| `Wine/wineboot/wineserver is missing` | Install Wine yourself; `wine doctor` shows layout and which prefix architectures are possible. |
| `PE32+ cannot run in a win32 prefix` / `64-bit executable requires a 64-bit operating system` | Real limits (e.g. Arch86). Compatibility says Likely incompatible; nothing is forced. |
| Prefix `failed` but files look fine | `prefix check ID` then `prefix recover ID` (metadata-only, 2.0 logic). |
| Game exits at once | `wine logs GAME` (redacted), `wine status GAME`; try `wine config GAME --dll-override …`/`--virtual-desktop`. A graphical session is required: set DISPLAY/WAYLAND_DISPLAY. |
| `audio doctor`: no-audio-device in a VM | The VM has no sound hardware. Add the QEMU flags it prints on the host and restart the VM yourself. |
| `audio doctor`: alsa-direct | Not a problem: the VM has a sound card and ALSA playback; there is just no PulseAudio/PipeWire server (Wine can use ALSA directly). |
| `audio doctor`: device-without-server | `systemctl --user enable --now pipewire pipewire-pulse wireplumber`; make sure `XDG_RUNTIME_DIR` is set (KMSCON/su sessions often lack it). |
| Browser audio: "authentication failed" | Wrong token; use the one in `~/.config/arch-games-audio-bridge/token` on the host. 5 failures/min are throttled. |
| Browser audio: capture error / "no audio within 5 s" | The sink or VM output is wrong: `…remote doctor`; check QEMU `out.name=` and that the VM is playing. |
| Browser audio silent after clicking | Click "Enable audio" (autoplay policy); check mute/volume and the buffer figure; Safari needs the click on this page. |
| A Pacman game installed but `launch` says no executable was found | See NATIVE_3.md: `arch-games discover GAME --all --json`, then `launcher register` for an owned program. |
| A native game launches but is silent | Save its audio settings as launch arguments/environment (NATIVE_3.md, OpenTTD example); check `audio doctor`. |
| `Launcher is missing or no longer belongs to the package` | `arch-games launcher rediscover GAME` (your settings are kept). |
| `arch-games web` says "Operation not implemented" | That was a 3.0 packaging bug fixed in the real-world-testing round: update (`git pull`, `sh arch86/games/install.sh`). |
| GameVault tab says to connect although you are connected | Fixed: the tab now explains the actual state; `a` toggles the compatibility filter, `S` syncs. |
| Web GUI: Sign in loops | Open the exact URL printed by `arch-games web` (token in `#token=`); cookies must be enabled for 127.0.0.1. |
| Web GUI: `Unrecognised Host header` | You opened it through another hostname; start with `--allowed-host NAME:PORT` (remote use needs `--allow-remote`). |
| TUI garbled | `arch-games menu --ascii` or `TERM=xterm`; `NO_COLOR=1` disables colour; `arch-games menu --plain` is the line menu. |
| `Local state database is unavailable or damaged` | Nothing was modified. Copy a backup from `data/db-backups/` over `state.db` while 3.0 is not running. |
| Slow `doctor --verbose` | It probes graphics tools (≈0.5 s); plain `doctor` skips them. |

Redaction: `wine logs` and diagnostics redact by default. Review any log before sharing it, and use `--raw` only for yourself.
