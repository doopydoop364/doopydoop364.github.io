# Complete CLI reference

Global --state-dir PATH precedes the command and isolates all state. --help is available at every level. --yes bypasses the application confirmation for the requested install/removal/prefix operation; Pacman retains its own confirmation. Launch and download add/run are explicit user actions.

## arch-games

```text
usage: arch-games [-h] [--version] [--state-dir STATE_DIR]
                  {menu,list,doctor,info,details,reports,analyze,web,install,uninstall,installed,gamevault,download,prefix,launch,history,feedback,settings} ...

Local-first Arch Linux game library

positional arguments:
  {menu,list,doctor,info,details,reports,analyze,web,install,uninstall,installed,gamevault,download,prefix,launch,history,feedback,settings}
    menu                Keyboard terminal interface
    list                Search the combined catalog
    doctor              Application integrity and optional prerequisites
    info                Measured system profile (JSON)
    details             Game details and compatibility report
    reports             Compatibility reports, including unknown entries
    analyze             Inspect a local executable/archive without running it
    web                 Read-only localhost gallery
    install             Install a Pacman game or extract a local game archive
    uninstall           Remove a managed game; archives/prefixes retained
    installed           Show installed games
    gamevault           Connect and browse GameVault
    download            Persistent authorized GameVault download queue
    prefix              Isolated per-game Wine environments
    launch              Explicitly launch a selected installed executable
    history             Local launch history
    feedback            Local user compatibility results; never uploaded
    settings            Show or change local preferences

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --state-dir STATE_DIR
                        Isolated configuration/data/cache root
```

## arch-games menu

```text
usage: arch-games menu [-h] [--plain] [--source {all,pacman,gamevault}]
                       [--search SEARCH] [--category CATEGORY] [--all]
                       [--compatible] [--installed] [--wine] [--verified]
                       [--offline] [--json]
                       [--sort {title,source,category,status}]

options:
  -h, --help            show this help message and exit
  --plain
  --source {all,pacman,gamevault}
  --search SEARCH
  --category CATEGORY
  --all
  --compatible
  --installed
  --wine
  --verified
  --offline
  --json
  --sort {title,source,category,status}
```

## arch-games list

```text
usage: arch-games list [-h] [--source {all,pacman,gamevault}]
                       [--search SEARCH] [--category CATEGORY] [--all]
                       [--compatible] [--installed] [--wine] [--verified]
                       [--offline] [--json]
                       [--sort {title,source,category,status}]

options:
  -h, --help            show this help message and exit
  --source {all,pacman,gamevault}
  --search SEARCH
  --category CATEGORY
  --all
  --compatible
  --installed
  --wine
  --verified
  --offline
  --json
  --sort {title,source,category,status}
```

## arch-games doctor

```text
usage: arch-games doctor [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games info

```text
usage: arch-games info [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games details

```text
usage: arch-games details [-h] [--offline] game

positional arguments:
  game

options:
  -h, --help  show this help message and exit
  --offline
```

## arch-games reports

```text
usage: arch-games reports [-h] [--source {all,pacman,gamevault}]
                          [--search SEARCH] [--category CATEGORY] [--all]
                          [--compatible] [--installed] [--wine] [--verified]
                          [--offline] [--json]
                          [--sort {title,source,category,status}]

options:
  -h, --help            show this help message and exit
  --source {all,pacman,gamevault}
  --search SEARCH
  --category CATEGORY
  --all
  --compatible
  --installed
  --wine
  --verified
  --offline
  --json
  --sort {title,source,category,status}
```

## arch-games analyze

```text
usage: arch-games analyze [-h] [--game GAME] [--member MEMBER]
                          [--graphics {2d,3d,unknown}] [--ram-mb RAM_MB]
                          [--vulkan] [--terminal]
                          path

positional arguments:
  path

options:
  -h, --help            show this help message and exit
  --game GAME           Save evidence against this catalog ID
  --member MEMBER       Explicit archive executable member to use as evidence
  --graphics {2d,3d,unknown}
  --ram-mb RAM_MB
  --vulkan
  --terminal            User assertion: game needs no graphical session
```

## arch-games web

```text
usage: arch-games web [-h] [--port PORT] [--source {all,pacman,gamevault}]
                      [--search SEARCH] [--category CATEGORY] [--all]
                      [--compatible] [--installed] [--wine] [--verified]
                      [--offline] [--json]
                      [--sort {title,source,category,status}]

options:
  -h, --help            show this help message and exit
  --port PORT
  --source {all,pacman,gamevault}
  --search SEARCH
  --category CATEGORY
  --all
  --compatible
  --installed
  --wine
  --verified
  --offline
  --json
  --sort {title,source,category,status}
```

## arch-games install

```text
usage: arch-games install [-h] [--archive ARCHIVE] [--title TITLE] [--yes]
                          [--reinstall] [--timeout TIMEOUT]
                          game

positional arguments:
  game

options:
  -h, --help         show this help message and exit
  --archive ARCHIVE
  --title TITLE
  --yes
  --reinstall
  --timeout TIMEOUT
```

## arch-games uninstall

```text
usage: arch-games uninstall [-h] [--yes] [--purge] game

positional arguments:
  game

options:
  -h, --help  show this help message and exit
  --yes
  --purge     Delete extracted files instead of retaining a backup
```

## arch-games installed

```text
usage: arch-games installed [-h] [--source {all,pacman,gamevault}]
                            [--search SEARCH] [--category CATEGORY] [--all]
                            [--compatible] [--installed] [--wine] [--verified]
                            [--offline] [--json]
                            [--sort {title,source,category,status}]

options:
  -h, --help            show this help message and exit
  --source {all,pacman,gamevault}
  --search SEARCH
  --category CATEGORY
  --all
  --compatible
  --installed
  --wine
  --verified
  --offline
  --json
  --sort {title,source,category,status}
```

## arch-games gamevault

```text
usage: arch-games gamevault [-h]
                            {connect,status,disconnect,list,search,details} ...

positional arguments:
  {connect,status,disconnect,list,search,details}

options:
  -h, --help            show this help message and exit
```

## arch-games gamevault connect

```text
usage: arch-games gamevault connect [-h] [--url URL] [--allow-http]
                                    [--api-key]

options:
  -h, --help    show this help message and exit
  --url URL
  --allow-http
  --api-key
```

## arch-games gamevault status

```text
usage: arch-games gamevault status [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games gamevault disconnect

```text
usage: arch-games gamevault disconnect [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games gamevault list

```text
usage: arch-games gamevault list [-h] [--offline]

options:
  -h, --help  show this help message and exit
  --offline
```

## arch-games gamevault search

```text
usage: arch-games gamevault search [-h] [--offline] query

positional arguments:
  query

options:
  -h, --help  show this help message and exit
  --offline
```

## arch-games gamevault details

```text
usage: arch-games gamevault details [-h] [--offline] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --offline
```

## arch-games download

```text
usage: arch-games download [-h] {add,run,list,cancel,retry} ...

positional arguments:
  {add,run,list,cancel,retry}

options:
  -h, --help            show this help message and exit
```

## arch-games download add

```text
usage: arch-games download add [-h] [--sha256 SHA256] [--max-gb MAX_GB]
                               [--offline]
                               game

positional arguments:
  game

options:
  -h, --help       show this help message and exit
  --sha256 SHA256
  --max-gb MAX_GB
  --offline
```

## arch-games download run

```text
usage: arch-games download run [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games download list

```text
usage: arch-games download list [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games download cancel

```text
usage: arch-games download cancel [-h] [--cleanup] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --cleanup
```

## arch-games download retry

```text
usage: arch-games download retry [-h] [--restart] id

positional arguments:
  id

options:
  -h, --help  show this help message and exit
  --restart
```

## arch-games prefix

```text
usage: arch-games prefix [-h] {list,create,check,backup,remove} ...

positional arguments:
  {list,create,check,backup,remove}

options:
  -h, --help            show this help message and exit
```

## arch-games prefix list

```text
usage: arch-games prefix list [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games prefix create

```text
usage: arch-games prefix create [-h] [--arch {win32,win64}] [--yes] game

positional arguments:
  game

options:
  -h, --help            show this help message and exit
  --arch {win32,win64}
  --yes
```

## arch-games prefix check

```text
usage: arch-games prefix check [-h] game

positional arguments:
  game

options:
  -h, --help  show this help message and exit
```

## arch-games prefix backup

```text
usage: arch-games prefix backup [-h] game

positional arguments:
  game

options:
  -h, --help  show this help message and exit
```

## arch-games prefix remove

```text
usage: arch-games prefix remove [-h] [--yes] [--purge] game

positional arguments:
  game

options:
  -h, --help  show this help message and exit
  --yes
  --purge
```

## arch-games launch

```text
usage: arch-games launch [-h] [--method {auto,native,wine,dosbox,custom}]
                         [--prefix PREFIX] [--arg ARG] [--env ENV]
                         [--command COMMAND] [--allow-external] [--dry-run]
                         [--interactive]
                         game executable

positional arguments:
  game
  executable

options:
  -h, --help            show this help message and exit
  --method {auto,native,wine,dosbox,custom}
  --prefix PREFIX
  --arg ARG
  --env ENV
  --command COMMAND     Explicit JSON argument array for custom method; {exe}
                        expands to the path
  --allow-external
  --dry-run
  --interactive         Attach terminal input/output instead of capturing game
                        output
```

## arch-games history

```text
usage: arch-games history [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games feedback

```text
usage: arch-games feedback [-h] {list,set} ...

positional arguments:
  {list,set}

options:
  -h, --help  show this help message and exit
```

## arch-games feedback list

```text
usage: arch-games feedback list [-h]

options:
  -h, --help  show this help message and exit
```

## arch-games feedback set

```text
usage: arch-games feedback set [-h] [--notes NOTES] [--launch LAUNCH]
                               [--manual]
                               game {works,issues,slow,fails,untested}

positional arguments:
  game
  {works,issues,slow,fails,untested}

options:
  -h, --help            show this help message and exit
  --notes NOTES
  --launch LAUNCH
  --manual
```

## arch-games settings

```text
usage: arch-games settings [-h] [{show,set}] [key] [value]

positional arguments:
  {show,set}
  key
  value

options:
  -h, --help  show this help message and exit
```

## Common workflows

```sh
arch-games list --source pacman --search puzzle
arch-games list --source gamevault --all --offline
arch-games reports --all --json
arch-games analyze Game.zip --member Game/Game.exe --game "gamevault:12@34" --graphics 2d
arch-games download retry ID --restart
arch-games download run
arch-games install local:example --archive ./Game.zip --title Example
arch-games launch local:example Game/Game.exe --arg=--fullscreen --env WINEDEBUG=-all --dry-run
arch-games feedback set local:example issues --notes "Audio glitches"
```

Exit status: 0 successful command, 1 operational failure, 2 argument error, 130 cancellation. A successful game exit is recorded but never marks it verified. A completed download reports checksum-verified, size-only or unverified explicitly. Legacy commands menu/web/list/info/install PACKAGE and all three aliases remain supported.
