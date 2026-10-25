# Command-line reference (3.0)

`arch-games` (aliases `arch86-games`, `arch64-games`) is complete without a web server or a graphical session; browsing, searching, diagnosing and
installing work over SSH, tmux and KMSCON. Global flags: `--state-dir DIR` (isolated config/data/cache, for testing), `--non-interactive` (never prompt),
`--debug`. With no command, `arch-games` opens the terminal UI (see INTERFACES.md / below); `--plain` gives the line-based menu.

## Game names

Every command that takes a game accepts: the canonical ID (`gamevault:32`, `gamevault:32@34`, `pacman:supertux`, `local:foo`), a package name, an alias you
defined, or the title. Resolution order: exact ID → exact alias → exact title → case-insensitive title → normalized title (punctuation/accents/whitespace) →
a *unique* fuzzy match. Several matches are never guessed: on a terminal you get a numbered picker, otherwise an error lists the candidate IDs, so scripts are deterministic.
Non-exact matches are announced on stderr (`Resolved 'undertle' -> Undertale [gamevault:32] (fuzzy match)`).

    arch-games alias add undertale gamevault:32
    arch-games launch undertale          # also: info, install, analyze, settings game, wine status, saves ..., compat show ...

## The main workflows

    arch-games install undertale                 # resolve, reuse/download, verify, inspect, plan; stops for approval
    arch-games install undertale --plan-only     # show the plan, run nothing (a GameVault download still happens)
    arch-games install undertale --yes           # approve extraction/prefix creation
    arch-games install undertale --yes --approve-installer   # also run the downloaded installer under Wine
    arch-games install undertale --status        # saved job; re-running the same command resumes it
    arch-games install undertale --abort         # cancel; extracted files go to backups, the prefix is kept
    arch-games install ID --archive FILE.zip     # user-supplied archive or setup .exe
    arch-games install ID --directory DIR        # register an existing installation in place
    arch-games discover undertale                # ranked executable candidates with evidence (nothing is run)
    arch-games discover undertale --register 1   # confirm one; stored with its evidence
    arch-games launch undertale                  # registered launcher, its prefix and working directory
    arch-games launch undertale --dry-run        # exact argv/cwd/prefix, no process
    arch-games doctor [--verbose] [--json]       # application, database, storage, Wine, audio, remote audio, web
    arch-games repair undertale [--apply]        # plan (default) / run only the safe automatic steps

`--yes` alone never runs a downloaded installer. 2.0's `install ID --archive F` (extract only) is now `install ID --archive F --extract-only`.
Pacman games keep the 2.0 behaviour (`sudo pacman -S --needed`, after a prompt).

## Other command groups

`wine doctor|runtimes|register-runtime|status|logs|config|apply-version`, `prefix list|create|check|recover|backup|backups|restore|inspect|export|remove` (see WINE_3.md);
`audio doctor|devices|test|remote ...` (AUDIO_3.md, REMOTE_AUDIO_3.md); `compat show|list|set|export|import`, `machine show|name` (COMPATIBILITY_3.md);
`saves discover|paths|add|remove|backup|list|show|verify|restore`; `history`, `playtime`, `performance`, `benchmark` (explicit only), `launch --monitor`;
`favorite`, `tag`, `notes`, `title`, `search`, `recommend`, `interests`, `sync export|import`; `download add|run|list|pause|retry|cancel|priority|dedupe`; `web` (WEB_GUI_3.md).

## Generated reference

Generated from the argument parser by `tools/gen_cli_reference.py` (a test fails if this block is stale).

<!-- BEGIN GENERATED REFERENCE -->

#### `menu`

Keyboard terminal interface

```
arch-games menu [-h] [--plain] [--ascii] [--source {all,pacman,gamevault}] [--search SEARCH] [--category CATEGORY] [--all] [--compatible] [--installed] [--wine] [--verified] [--offline] [--json] [--sort {title,source,category,status}]
```
- `--ascii` — ASCII-only drawing (also set by TERM=linux)

#### `list`

Search the combined catalog

```
arch-games list [-h] [--source {all,pacman,gamevault}] [--search SEARCH] [--category CATEGORY] [--all] [--compatible] [--installed] [--wine] [--verified] [--offline] [--json] [--sort {title,source,category,status}]
```

#### `doctor`

Application, data, Wine, audio and storage diagnostics

```
arch-games doctor [-h] [--verbose] [--json] [--online] [--redact]
```
- `--online` — Also contact GameVault and the audio bridge (off by default)
- `--redact` — Hide home/data paths, IPs and tokens so the output can be shared

#### `repair`

Plan (default / --dry-run) or apply safe repairs for one game

```
arch-games repair [-h] [--dry-run] [--apply] game
```
- `--dry-run` — Show the plan (this is also the default)
- `--apply` — Run the safe automatic steps only

#### `info`

Measured system profile, or everything known about one game

```
arch-games info [-h] [game]
```

#### `details`

Game details and compatibility report

```
arch-games details [-h] [--offline] game
```

#### `reports`

Compatibility reports, including unknown entries

```
arch-games reports [-h] [--source {all,pacman,gamevault}] [--search SEARCH] [--category CATEGORY] [--all] [--compatible] [--installed] [--wine] [--verified] [--offline] [--json] [--sort {title,source,category,status}]
```

#### `analyze`

Inspect a local executable/archive without running it

```
arch-games analyze [-h] [--debug] [--game GAME] [--member MEMBER] [--graphics {2d,3d,unknown}] [--ram-mb RAM_MB] [--vulkan] [--terminal] path
```
- `path` — Local file, or a game name to analyze its registered launcher
- `--debug` — Show redacted archive-worker diagnostics
- `--game` — Save evidence against this catalog ID
- `--member` — Explicit archive executable member to use as evidence
- `--terminal` — User assertion: game needs no graphical session

#### `web`

Optional web GUI (loopback by default, token login)

```
arch-games web [-h] [--port PORT] [--host HOST] [--allow-remote] [--allowed-host HOST:PORT]
```
- `--allow-remote` — Required to bind a non-loopback address
- `--allowed-host` — Extra Host header accepted (remote use)

#### `install`

Install a Pacman game or extract a local game archive

```
arch-games install [-h] [--archive ARCHIVE] [--title TITLE] [--yes] [--reinstall] [--timeout TIMEOUT] [--directory DIRECTORY] [--plan-only] [--installer INSTALLER] [--approve-installer] [--silent-inno] [--test-launch] [--test-seconds TEST_SECONDS] [--prefix-arch {win32,win64}] [--sha256 SHA256] [--offline] [--abort] [--status] [--extract-only] game
```
- `--directory` — Use an already-extracted/installed game folder in place
- `--plan-only` — Show the plan; execute nothing
- `--installer` — Archive member to run when several installers exist
- `--approve-installer` — Explicitly approve running the downloaded installer
- `--silent-inno` — Pass Inno Setup silent flags (only if evidence shows Inno)
- `--test-launch` — Supervised, time-limited launch after install
- `--sha256` — Expected SHA-256 of the archive
- `--abort` — Cancel the unfinished job (files go to backups)
- `--status` — Show the saved installation job
- `--extract-only` — 2.0 behaviour: extract --archive, never run anything

#### `uninstall`

Remove a managed game; archives/prefixes retained

```
arch-games uninstall [-h] [--yes] [--purge] game
```
- `--purge` — Delete extracted files instead of retaining a backup

#### `installed`

Show installed games

```
arch-games installed [-h] [--source {all,pacman,gamevault}] [--search SEARCH] [--category CATEGORY] [--all] [--compatible] [--installed] [--wine] [--verified] [--offline] [--json] [--sort {title,source,category,status}]
```

#### `gamevault`

Connect and browse GameVault

```
arch-games gamevault [-h] {connect,status,disconnect,list,search,details} ...
```

#### `gamevault connect`

```
arch-games gamevault connect [-h] [--url URL] [--allow-http] [--api-key]
```

#### `gamevault status`

```
arch-games gamevault status [-h]
```

#### `gamevault disconnect`

```
arch-games gamevault disconnect [-h]
```

#### `gamevault list`

```
arch-games gamevault list [-h] [--offline]
```

#### `gamevault search`

```
arch-games gamevault search [-h] [--offline] query
```

#### `gamevault details`

```
arch-games gamevault details [-h] [--offline] id
```

#### `download`

Persistent authorized GameVault download queue

```
arch-games download [-h] {add,run,pause,priority,dedupe,list,cancel,retry} ...
```

#### `download add`

```
arch-games download add [-h] [--sha256 SHA256] [--max-gb MAX_GB] [--offline] [--priority PRIORITY] [--force] game
```
- `--priority` — 0 (first) .. 9 (last)
- `--force` — Download again even if this exact archive is already stored

#### `download run`

```
arch-games download run [-h]
```

#### `download pause`

Stop and keep the partial file; 'retry' resumes

```
arch-games download pause [-h] id
```

#### `download priority`

```
arch-games download priority [-h] id level
```

#### `download dedupe`

Find identical archives; --apply hard-links the duplicates (nothing is deleted)

```
arch-games download dedupe [-h] [--apply]
```

#### `download list`

```
arch-games download list [-h]
```

#### `download cancel`

```
arch-games download cancel [-h] [--cleanup] id
```

#### `download retry`

```
arch-games download retry [-h] [--restart] id
```

#### `prefix`

Isolated per-game Wine environments

```
arch-games prefix [-h] {list,create,check,backup,remove,recover,inspect,backups,export,restore} ...
```

#### `prefix list`

```
arch-games prefix list [-h]
```

#### `prefix create`

```
arch-games prefix create [-h] [--arch {win32,win64}] [--timeout TIMEOUT] [--yes] game
```
- `--timeout` — Initialization deadline (after version probe), default 120 seconds

#### `prefix check`

```
arch-games prefix check [-h] game
```

#### `prefix backup`

```
arch-games prefix backup [-h] game
```

#### `prefix remove`

```
arch-games prefix remove [-h] [--yes] [--purge] game
```

#### `prefix recover`

```
arch-games prefix recover [-h] [--timeout TIMEOUT] game
```
- `--timeout` — Wait for stable existing prefix files, default 5 seconds

#### `prefix inspect`

```
arch-games prefix inspect [-h] game
```

#### `prefix backups`

```
arch-games prefix backups [-h] game
```

#### `prefix export`

```
arch-games prefix export [-h] game
```

#### `prefix restore`

Restore a prefix backup (preview unless --apply)

```
arch-games prefix restore [-h] [--apply] game backup
```

#### `wine`

Wine runtimes, diagnostics and per-game settings

```
arch-games wine [-h] {doctor,runtimes,register-runtime,status,logs,config,apply-version} ...
```

#### `wine doctor`

```
arch-games wine doctor [-h] [--json]
```

#### `wine runtimes`

```
arch-games wine runtimes [-h]
```

#### `wine register-runtime`

Register an already-installed wine binary by path

```
arch-games wine register-runtime [-h] name path
```

#### `wine status`

```
arch-games wine status [-h] game
```

#### `wine logs`

```
arch-games wine logs [-h] [--lines LINES] [--raw] game
```
- `--raw` — Do not redact private paths and addresses

#### `wine config`

Per-game Wine settings (stored; applied at launch)

```
arch-games wine config [-h] [--runtime RUNTIME] [--windows-version WINDOWS_VERSION] [--dll-override DLL=MODE] [--env ENV] [--virtual-desktop VIRTUAL_DESKTOP] [--debug DEBUG] [--clear] game
```
- `--virtual-desktop` — WIDTHxHEIGHT, or 'off'
- `--debug` — WINEDEBUG channels, or 'off'

#### `wine apply-version`

Run winecfg to set the saved Windows version in the prefix

```
arch-games wine apply-version [-h] game
```

#### `audio`

Audio diagnostics and a bounded sound test

```
arch-games audio [-h] {doctor,devices,remote,test} ...
```

#### `audio doctor`

```
arch-games audio doctor [-h] [--json]
```

#### `audio devices`

```
arch-games audio devices [-h]
```

#### `audio remote`

Optional browser audio via the host bridge (off by default)

```
arch-games audio remote [-h] {configure,status,disable,instructions,url} ...
```

#### `audio remote configure`

```
arch-games audio remote configure [-h] --url URL --vm VM [--allow-http]
```
- `--allow-http` — Needed for http:// (use only inside Tailscale/LAN)

#### `audio remote status`

```
arch-games audio remote status [-h]
```

#### `audio remote disable`

```
arch-games audio remote disable [-h]
```

#### `audio remote instructions`

```
arch-games audio remote instructions [-h]
```

#### `audio remote url`

```
arch-games audio remote url [-h]
```

#### `audio test`

```
arch-games audio test [-h] [--seconds SECONDS] [--backend {auto,pulse,alsa}]
```

#### `machine`

This machine's identity for compatibility records

```
arch-games machine [-h] {show,name} ...
```

#### `machine show`

```
arch-games machine show [-h]
```

#### `machine name`

```
arch-games machine name [-h] name
```

#### `compat`

Per-machine compatibility records (local; never uploaded)

```
arch-games compat [-h] {show,list,set,export,import} ...
```

#### `compat show`

```
arch-games compat show [-h] game
```

#### `compat list`

```
arch-games compat list [-h] [--machine MACHINE]
```
- `--machine` — 'this', 'all' or a machine id

#### `compat set`

Record what you observed on this machine

```
arch-games compat set [-h] [--gameplay {unverified,works,issues,slow,fails}] [--audio {unknown,works,issues,none}] [--issue ISSUE] [--notes NOTES] game
```
- `--issue` — Add a known issue note

#### `compat export`

```
arch-games compat export [-h] [--output OUTPUT]
```
- `--output` — File to write (default: stdout)

#### `compat import`

```
arch-games compat import [-h] file
```

#### `saves`

Find, back up and restore game saves (local only)

```
arch-games saves [-h] {discover,paths,list,backup,add,remove,show,verify,restore} ...
```

#### `saves discover`

```
arch-games saves discover [-h] game
```

#### `saves paths`

```
arch-games saves paths [-h] game
```

#### `saves list`

```
arch-games saves list [-h] game
```

#### `saves backup`

```
arch-games saves backup [-h] [--reason {manual,pre-reinstall,pre-prefix-change}] game
```

#### `saves add`

Register / unregister a save folder you choose

```
arch-games saves add [-h] game path
```

#### `saves remove`

Register / unregister a save folder you choose

```
arch-games saves remove [-h] game path
```

#### `saves show`

```
arch-games saves show [-h] game backup
```

#### `saves verify`

```
arch-games saves verify [-h] game backup
```

#### `saves restore`

Preview (default) or apply a restore

```
arch-games saves restore [-h] [--apply] [--yes] game backup
```

#### `search`

Search the library by name, alias, tag, source, platform, state, compatibility

```
arch-games search [-h] [--source {pacman,gamevault,local}] [--platform {windows,linux,dos}] [--installed {yes,no}] [--compat COMPAT] [--tag TAG] [--favorites] [--limit LIMIT] [--json] [query]
```
- `--compat` — e.g. 'Verified working', 'Likely compatible'

#### `recommend`

Explainable suggestions from your own favorites, playtime, tags and interests

```
arch-games recommend [-h] [--limit LIMIT]
```

#### `interests`

Keywords you like (used only for local recommendations)

```
arch-games interests [-h] {list,add,remove} ...
```

#### `interests list`

```
arch-games interests list [-h]
```

#### `interests add`

```
arch-games interests add [-h] keyword [keyword ...]
```

#### `interests remove`

```
arch-games interests remove [-h] keyword [keyword ...]
```

#### `sync`

Private file-based bundle of favorites, aliases, notes, tags, titles, compatibility

```
arch-games sync [-h] {export,import} ...
```

#### `sync export`

```
arch-games sync export [-h] [--output OUTPUT]
```

#### `sync import`

```
arch-games sync import [-h] [--prefer-remote] file
```

#### `favorite`

Mark favorite games

```
arch-games favorite [-h] {list,add,remove,toggle} ...
```

#### `favorite list`

```
arch-games favorite list [-h]
```

#### `favorite add`

```
arch-games favorite add [-h] game
```

#### `favorite remove`

```
arch-games favorite remove [-h] game
```

#### `favorite toggle`

```
arch-games favorite toggle [-h] game
```

#### `launch`

Explicitly launch a selected installed executable

```
arch-games launch [-h] [--method {auto,native,wine,dosbox,custom}] [--prefix PREFIX] [--arg ARG] [--env ENV] [--command COMMAND] [--allow-external] [--monitor] [--dry-run] [--interactive] game [executable]
```
- `executable` — Optional; omitted = use the registered launcher for this game
- `--command` — Explicit JSON argument array for custom method; {exe} expands to the path
- `--monitor` — Record CPU/memory samples for this launch (also: setting performance_monitoring)
- `--interactive` — Attach terminal input/output instead of capturing game output

#### `alias`

Friendly names for games

```
arch-games alias [-h] {add,list,remove} ...
```

#### `alias add`

```
arch-games alias add [-h] [--force] alias game
```

#### `alias list`

```
arch-games alias list [-h]
```

#### `alias remove`

```
arch-games alias remove [-h] alias
```

#### `discover`

Rank the executables that may start a game (nothing is run)

```
arch-games discover [-h] [--dir DIR] [--all] [--json] [--register RANK] game
```
- `--dir` — Extra directory to search
- `--all` — Include uninstallers, installers, helpers
- `--register` — Confirm and register this candidate

#### `launcher`

Registered launcher per game

```
arch-games launcher [-h] {list,show,remove,register} ...
```

#### `launcher list`

```
arch-games launcher list [-h]
```

#### `launcher show`

```
arch-games launcher show [-h] game
```

#### `launcher remove`

```
arch-games launcher remove [-h] game
```

#### `launcher register`

```
arch-games launcher register [-h] [--prefix PREFIX] [--cwd CWD] [--arg ARG] [--env ENV] [--allow-external] [--force-role] [--yes] game [executable]
```
- `executable` — Absolute path; omitted = choose from discovery

#### `history`

Launch history (all games, or one)

```
arch-games history [-h] [--limit LIMIT] [game]
```

#### `playtime`

Playtime estimates from launch history

```
arch-games playtime [-h] [game]
```

#### `performance`

Stored performance measurements (launches nothing)

```
arch-games performance [-h] game
```

#### `benchmark`

Explicitly launch a game and measure it for a fixed time

```
arch-games benchmark [-h] [--seconds SECONDS] [--yes] game
```

#### `title`

Custom display titles

```
arch-games title [-h] {set,clear} ...
```

#### `title set`

```
arch-games title set [-h] game title
```

#### `title clear`

```
arch-games title clear [-h] game
```

#### `notes`

Show or set your notes for a game

```
arch-games notes [-h] game [text]
```

#### `tag`

User-defined tags

```
arch-games tag [-h] {add,remove,list} ...
```

#### `tag add`

```
arch-games tag add [-h] game tag
```

#### `tag remove`

```
arch-games tag remove [-h] game tag
```

#### `tag list`

```
arch-games tag list [-h] [game]
```

#### `feedback`

Local user compatibility results; never uploaded

```
arch-games feedback [-h] {list,set} ...
```

#### `feedback list`

```
arch-games feedback list [-h]
```

#### `feedback set`

```
arch-games feedback set [-h] [--notes NOTES] [--launch LAUNCH] [--manual] game {works,issues,slow,fails,untested}
```

#### `catalog`

Explicit local catalog identity assertions

```
arch-games catalog [-h] {identify,forget} ...
```

#### `catalog identify`

```
arch-games catalog identify [-h] --canonical CANONICAL --build BUILD [--edition EDITION] game
```

#### `catalog forget`

```
arch-games catalog forget [-h] game
```

#### `settings`

Show or change local preferences

```
arch-games settings [-h] [{show,set,game}] [key] [value]
```

<!-- END GENERATED REFERENCE -->
