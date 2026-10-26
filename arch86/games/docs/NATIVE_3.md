# Native Linux games and per-game launch settings

Games from Pacman (and any native program you register) launch **without Wine**, with arguments, environment variables and named launch profiles
that you save once. The same settings work for Wine games. Everything below is exercised by the automated tests with a fake Pacman database;
the first real run is on your VM (see "Try it on a real machine").

## The workflow
```sh
arch-games search openttd            # catalog entry: OpenTTD  [pacman:openttd]
arch-games install openttd           # asks first; runs `sudo pacman -S --needed -- openttd` only after your approval
arch-games discover openttd          # package-aware: which program in the package starts the game
arch-games launch openttd --dry-run  # what would run: program, arguments, environment overrides, working directory, runtime
arch-games launch openttd
```
* `install` verifies against the real package database that the package is installed (the exit status is not trusted), then finds the launcher. If the choice is
  unambiguous it registers it; if not it lists candidates and says *"Installation succeeded; launcher configuration still required."* (installation is never reported as failed
  because of that). Re-running `install` (or `discover GAME --register N`) retries the launcher step without reinstalling.
* `launch` on a game that has no launcher yet performs the same discovery (no Wine involved) and asks when it is not clear. `--dry-run` never registers anything.
* Installed state for Pacman games comes from the local package database (`pacman -Q`), so a game you installed by hand, before Arch Games, or during a system update is recognised,
  and one removed behind its back is noticed.

### Status vocabulary (identical in the CLI `info`, the TUI details and the web page)
| Status | Meaning |
|---|---|
| `available` | in the repositories, not installed |
| `installed-no-launcher` | installed through Pacman, no launcher registered yet |
| `ready` | installed and the registered launcher is valid |
| `launcher-invalid` | the executable is missing, or no longer owned by the package (for example after an upgrade moved it) |
| `package-removed` | a launcher is registered but the package is no longer installed |

## How the executable is found (and why it can be trusted)
1. `pacman -Q` says whether the package is installed; `pacman -Ql` lists the files it owns. /usr/bin is **never** scanned blindly.
2. Candidates are owned, executable files in the package's program directories (`/usr/bin`, `/usr/games`, `/usr/lib/<x>/`, `/opt/...`) that are Linux ELF programs or `#!` wrapper scripts.
   Symlinks count only when they point at a file the package also owns. Files are inspected by header; **nothing is executed** to identify it.
3. The package's own `.desktop` entries are parsed (never run through a shell, field codes such as `%U` dropped, `env VAR=x` prefixes treated as hints, only programs the package owns accepted).
4. Helpers, updaters, crash reporters, uninstallers, installers, redistributables and config tools are excluded by name; the rest is ranked with explainable evidence (name matches the package,
   a desktop entry launches it, standard program directory, only launchable program in the package, ...). `discover GAME --all` shows the excluded ones too.
5. Only an unambiguous choice (a single launchable program, or exactly one desktop-entry program whose name matches the package) is registered automatically; otherwise you confirm.
6. Registration re-verifies ownership with `pacman -Qo`, and the same check runs again at every launch, so a tampered database record cannot point the launcher at an unowned program.
7. Paths must be under `/usr` or `/opt`. The game always runs as your user; sudo is used for nothing except the approved `pacman -S`.

After a package upgrade moves a program, the launch fails with a clear message (or, on a terminal, offers to rediscover). **`launcher rediscover GAME` keeps your arguments, environment and profiles**;
only the executable and package version change.

## Launch settings and profiles
Each launcher has a **default** profile (the historical top-level arguments/environment of the record) and optional named profiles. A profile is a complete configuration: arguments and environment
(it can also override working directory/runner in future; an `extras` field is reserved). A plain `launch` uses the default profile unless you chose another default; `launch --profile NAME` picks one.

```sh
arch-games launcher config openttd                        # view the effective configuration, status and the command it would run (changes nothing)
arch-games launcher arg add openttd -s sdl                # append arguments (taken literally; one argv entry each)
arch-games launcher arg list|set|clear openttd ...        # `arg remove openttd 2` or `arg remove openttd -- -s` (position, or text after --)
arch-games launcher env set openttd SDL_AUDIODRIVER alsa
arch-games launcher env set openttd AUDIODEV plughw:0,0
arch-games launcher env list|unset openttd ...            # `env policy` explains what is allowed
arch-games launcher profile create openttd silent         # copy of the default profile; then: env set --profile silent openttd ...
arch-games launcher profile list|show|default|delete ...
arch-games launcher validate openttd                      # checks everything, launches nothing
arch-games launcher reset openttd [--profile P] --yes     # clears a profile's arguments and environment (asks first)
arch-games launcher rediscover openttd                    # re-find the executable, settings kept
```
Put options such as `--profile P` *before* the game name in `arg add/set`; everything after the game is taken as arguments.

**Environment rules.** Names are letters, digits and `_`; values have no NUL or line breaks. Nothing is expanded or evaluated: a value like `$(x)` reaches the program as that literal text.
`WINEPREFIX`/`WINEARCH` are reserved (Arch Games manages them per game). Sensitive variables (`PATH`, `HOME`, `SHELL`, `LD_*`, `PYTHONPATH`, `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, ...)
are refused in the TUI and web GUI and in the CLI unless you add `--allow-sensitive`. Everything you do not override is inherited from your session (`DISPLAY`, `WAYLAND_DISPLAY`, `XDG_*`, `PULSE_SERVER`, ...).

### Real-world example: OpenTTD in the Arch64 VM
OpenTTD started but was silent; this configuration, **tested on that VM**, fixed the sound:
```
env SDL_AUDIODRIVER=alsa AUDIODEV=plughw:0,0 openttd -s sdl
```
Save it once and launch normally:
```sh
arch-games launcher env set openttd SDL_AUDIODRIVER alsa
arch-games launcher env set openttd AUDIODEV plughw:0,0
arch-games launcher arg add openttd -s sdl
arch-games launch openttd --dry-run     # Command: env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa /usr/bin/openttd -s sdl
```
This is *not* a default and not advice for other machines: `plughw:0,0` names that VM's virtual Intel HDA card, and `-s sdl` selects OpenTTD's SDL sound driver. On another system find your
device with `aplay -l` and your game's own audio options first. If you want it only sometimes, keep it in a separate profile.

## Compatibility evidence from native launches
A launch records its profile, arguments and environment overrides in the history and in this machine's compatibility record (variable *values* stay local: exports contain names only). The ELF
architecture of the registered program is measured. A program that starts is "Likely compatible" at most: **only your own confirmation** (`compat set`, or `v` in the TUI) makes it "Verified working",
and that confirmation is tied to the launch configuration you used. Nothing is assumed about frame rate or graphics acceleration.

## Interfaces
* **TUI:** `o` on a game opens *Launch settings* (details, executable and status, profile selector, arguments, environment, preview of the command, Save/Cancel, rediscover). See TUI_3.md.
* **Web GUI:** the game page shows the package status, executable and a launch-settings form (arguments, environment, profiles, "launch with this profile"). The executable, runner and working directory
  cannot be changed from the browser and sensitive variables are refused there; those stay CLI/TUI-only on purpose. Package installation (it needs sudo) is terminal-only. See WEB_GUI_3.md.

## Troubleshooting
| Symptom | What to do |
|---|---|
| `No game executables found` for a package | `discover GAME --all --json`: shows excluded helpers and notes. A program the package does not own, or outside /usr and /opt, is never offered; register an owned path with `launcher register GAME /usr/bin/NAME`. |
| `Launcher is missing or no longer belongs to the package` | `launcher rediscover GAME` (keeps settings). |
| `Package ... is no longer installed` | `install GAME`; your settings are still saved. |
| Game starts silent | `audio doctor`, then try the game's audio driver variables as in the OpenTTD example; save them with `launcher env set`. |
| Window does not open | The launch plan warns when no X11/Wayland session is visible; run from your desktop session. |
| A saved argument starting with `-` | `launcher arg add GAME -s sdl` works (taken literally); to remove it use `launcher arg remove GAME -- -s` or its position. |

## Data safety
No schema change: new fields (`kind`, `package`, `profiles`, `default_profile`, `extras`) are optional and old launcher records keep working unchanged (tested). Re-registering never resets settings
unless you pass the explicit reset. Nothing here deletes prefixes, downloads, credentials, history, compatibility records or save backups.

## Try it on a real machine
See the "Native games" part of TESTING_3.md. Automated tests use a fake Pacman and never install packages or call sudo.
