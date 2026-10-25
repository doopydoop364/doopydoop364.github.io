# Terminal and visual interfaces

`arch-games` opens eight sections: catalog, GameVault, compatible, installed,
downloads, reports, prefixes and settings. On a capable local terminal, arrows or
j/k move, Enter opens, digits select, q/Escape exits. Small screens show the
selected section and basic keys. Curses is standard-library and optional; import
or terminal setup failures fall back to plain text.

`arch-games menu --plain` works in KMSCON, SSH, tmux, dumb terminals and redirected
sessions. Catalog pages support n/p, /search, a to expose unknown entries, numbers
for details, and b to return. Selected details offer explicit install or queue
operations; downloads still require `download run`. Terminal control characters
are removed from untrusted titles/descriptions. Plain lists use ASCII fallbacks.
The CLI is available for every operation without a graphical display.

`arch-games web --all` opens no browser automatically. Open the printed localhost
URL in a browser inside the VM. `--port N` changes the port; zero chooses a free
local port. There is no option to bind publicly. Search, category, architecture
and compatibility filters run locally. Cards show descriptions, status and copied
terminal commands. Details show evidence and uncertainty. Artwork is fetched only
for matching library IDs when available, with category-art fallbacks. The gallery
polls local download state every five seconds. Metadata and compatibility are a
snapshot of startup; restart after analyzing games or changing configuration.

There are no browser actions that install packages, queue transfers or launch
games. Cross-origin requests and unexpected Host headers are refused; administrative
controls are not exposed. To view remotely, use an authenticated tunnel you manage.

Use `launch ID EXE --interactive` to attach terminal input/output for a native
terminal game. This mode records exit status but does not capture game output.

The Download Manager accepts run/retry/cancel/cleanup actions. The prefix section
accepts create/check/recover/backup/remove, with separate confirmation for changes. Settings
can connect/disconnect GameVault or set a supported preference from the menu.
