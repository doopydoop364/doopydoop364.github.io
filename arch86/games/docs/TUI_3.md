# Terminal UI (3.0)

`arch-games` opens the manager in any terminal, including SSH, tmux and KMSCON. Everything is available from the keyboard; the mouse is an optional extra.
`arch-games menu --plain` gives the old line menu, `--ascii` forces ASCII drawing (also automatic for `TERM=linux`), `NO_COLOR=1` turns colour off.

## Sections
Library, Installed, GameVault, Downloads, Compatibility, Wine, Audio, Settings, Diagnostics. Slow sections (Wine, Audio, Settings, Diagnostics) are computed once and reused when
you switch tabs; `r` refreshes from local data. Switching tabs never contacts the network. The only network use is the one-time GameVault synchronisation when the TUI starts
(`menu --offline` skips it) and `S` on the GameVault tab, which runs `gamevault list`.

## Keys
| Key | Action |
|---|---|
| Up/Down, j/k, PgUp/PgDn, Home/End | move |
| Tab / Shift-Tab, Left/Right, 1-9 | switch section |
| Enter / Esc | open details / go back |
| `/` | search (Enter keeps it, Esc clears it) |
| `l` `i` `d` `s` `w` `x` `c` | launch, install, queue download, settings, wine status, find executable, compatibility details (run the same CLI command, then return) |
| `v` | compatibility feedback panel for the selected game (see below) |
| `o` | launch settings for the selected game: arguments, environment, profiles (see below) |
| `f` | toggle favorite |
| `a` | toggle the compatibility filter for the current game list |
| `S` (GameVault tab) | sync the library now |
| `r` `?` `q` | refresh, help, quit |

### The `a` filter
* **GameVault** and **Installed** start by showing every game. Games without evidence are labelled `Unknown` (or `Requires configuration`, `Likely compatible`, ...), never hidden.
* **Library** starts with unverified GameVault entries hidden. Pressing `a` shows them. The status line always says how many games the filter hides.
* Empty lists say why: not connected, connected but never synchronised, synchronisation failed, empty catalog, no search match, or hidden by the filter.

### Compatibility feedback (`v`)
Opens a panel for the selected game with **Gameplay** (Works / Works with issues / Broken / Untested) and **Audio** (same choices), pre-set to what is stored for *this machine*.
Up/Down or Tab move between the two groups and the Save/Cancel buttons, Left/Right or Space change the choice, `s` or the Save button saves, Esc or Cancel leaves without changing anything.
Nothing is written while you navigate. Saving uses the same service as `arch-games compat set` (Gameplay Broken = `fails`, Audio Broken = `none`, Untested = `unverified`/`unknown`), only changed
facts get a new timestamp, other machines' records are untouched, and the game's classification is recomputed immediately.

## Mouse
Used when the terminal reports it (xterm-compatible emulators, also over SSH). The Linux console / KMSCON without mouse reporting simply stays keyboard-only; `ARCH_GAMES_NO_MOUSE=1` disables it.
Click a game to select it, click twice (or double-click) to open its details, click a section tab to switch, use the wheel to scroll the list (3 rows per notch), click `[key] ...` lines in the details to run them and
`[Esc] back` to return, click the options and the Save/Cancel buttons in the feedback panel. Hit testing is recomputed from the frame that is on screen, so it stays correct after a resize or scrolling.
Terminals that capture the mouse for selection need Shift held to select text.

## Launch settings (`o`)
Needs a registered launcher (otherwise it says to press `x` first). Shows the executable (and whether it is missing or no longer owned by its package), the runtime (native Linux or Wine), the status,
the selected **profile**, its **arguments** and **environment overrides**, and a live preview of the exact command. Edit a copy; nothing is stored until **Save** (`s`); **Cancel**/Esc discards
(a second Esc is needed when there are unsaved changes).

| Key | Action |
|---|---|
| Up/Down, Tab, PgUp/PgDn | move between items |
| Enter | edit the selected argument/variable, add a new one, or press the selected button |
| `a` / `e` | add an argument / an environment variable (`NAME=VALUE`) |
| `d` or `x` | delete the selected argument or variable |
| Left/Right or `[` `]` | switch profile |
| buttons | make the profile the default, new profile (copy), delete profile, rediscover executable (runs `launcher rediscover`, settings kept), Save, Cancel |
| Esc | cancel the text entry; otherwise cancel the panel |

Mouse: click an item to select it, click again (or double-click) to edit it, click buttons, the profile line (cycles profiles) and the add lines directly; the wheel scrolls. Sensitive variables (PATH, LD_*, HOME, ...)
and `WINEPREFIX` are refused with an explanation; use `arch-games launcher env set --allow-sensitive` if you really need one. The panel works in small terminals (it scrolls) and in ASCII mode.
The game details show the native package status (`Native Linux package openttd 15.3-1: Installed and ready to launch`) and a note when launch settings are saved.
