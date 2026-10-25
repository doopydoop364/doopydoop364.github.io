> **2.0 reference.** Arch Games 3.0 extends this; see WINE_3.md for the current behaviour. The text below is kept unchanged.

# Wine setup and isolated prefixes

Browsing never needs Wine. Review packages available for your own Arch or Arch
Linux 32 repositories, then install the appropriate supported runtime yourself.
For official x86-64 Arch, consult https://wiki.archlinux.org/title/Wine for the
current packaging and WoW64 transition details. Arch Linux 32 packages and prefix
capabilities differ; do not copy assumptions from official x86-64 Arch.
Arch Games does not run a package-install command to set up Wine or game DLLs.

`arch-games info` reports installed Wine/version but leaves win32/win64/WoW64
capabilities unknown until you provide evidence. Prefix creation is an explicit
probe using installed wineboot, never a downloaded game. Example:

```sh
arch-games prefix create 'gamevault:12@34' --arch win64
arch-games prefix check 'gamevault:12@34'
```

Use win32 only where installed Wine actually permits it. On modern x86-64 WoW64
Wine, a win64 prefix can run PE32 games; forcing WINEARCH=win32 may fail. The app
reports that failure and retains a private diagnostic log; it never silently
switches architectures. On i686, win64 is rejected. Wine creation disables the
mscoree/mshtml bootstrap to avoid automatic Mono/Gecko installer downloads. Games
requiring those components need separately reviewed user setup.

If you have independently verified a runtime capability, record a user assertion:
`arch-games settings set wine_wow64 true` (or wine_win32 / wine_win64).
`unknown` clears the assertion. This changes predictions, not gameplay verification.
A prefix's registry architecture and health are shown by `prefix check`.

Every game gets a separate prefix under the application data directory's prefixes/
subdirectory. `launch ID EXE` selects that prefix; `--prefix OTHER_ID` explicitly
shares another managed prefix. `--env KEY=VALUE` customizes a launch; WINEPREFIX
and WINEARCH are reserved for prefix management. `--arg=--fullscreen` forwards a
dash-prefixed argument. `--method custom --command '["/usr/bin/tool","{exe}"]'`
is an advanced, explicit local command array. Server launch parameters are ignored.

Stop games and Wine processes before `prefix backup ID` or `prefix remove ID`.
Backups preserve Wine drive symlinks without following them. Removal retains a
backup unless `--purge` is supplied. Do not put game data in system-wide prefixes.
Wine setup executables may install into the chosen prefix when explicitly launched;
files they create in drive_c are not automatically adopted as managed game files.
Use an explicit advanced launch configuration if you intend to run those files.

Software-rendered QEMU graphics are suitable for conservative experiments with
lighter games; Wine being installed does not imply playable DirectX 3D. Vulkan is
not assumed. DXVK/vkd3d installation and arbitrary DLL downloads are not automated.


## Initialization synchronization and recovery

New prefixes run wineboot once, then `wineserver -w` with that prefix's exact
WINEPREFIX. The server wait is bounded at 30 seconds and the total initialization
budget defaults to 120 seconds after the short version probe. Registry verification
then polls for at most five seconds within the remaining budget, requiring two
stable observations. `prefix create ID --timeout SECONDS` changes the total budget
(1..600); no Wine process is killed or reinitialization retried automatically.

`prefix check ID` reports structural health separately from the database state,
including missing/unreadable files, registry headers and architecture mismatches.
A pair of files merely existing does not establish a valid matching Wine registry.
Gameplay and graphics/runtime functionality are outside this health check.

If initial verification failed but initialization later finished:

```sh
arch-games prefix check gamevault:32
arch-games prefix recover gamevault:32
```

Recovery requires stable readable Wine registry headers, matching requested
architecture and a safe drive_c directory. It runs no Wine command and changes
only Arch Games metadata, retaining instance ID, logs, failure details, environment,
registry files, drive mappings, installed applications and saves. `--timeout N`
allows up to 120 seconds waiting for existing files (default five). Repeating create
for a failed/creating record with the same architecture performs this recovery;
it does not overwrite the prefix. The launcher points healthy stale records to
recover instead of presenting them as structurally incomplete.

See [investigation and regression evidence](PREFIX_RECOVERY_FIX.md). Wine 11.20's
[wineboot](https://github.com/wine-mirror/wine/blob/wine-11.20/programs/wineboot/wineboot.c)
returns after its operation, while registry persistence is handled separately by
[the server](https://github.com/wine-mirror/wine/blob/wine-11.20/server/registry.c).
The server's [-w option](https://github.com/wine-mirror/wine/blob/wine-11.20/server/main.c)
waits for the current server to terminate. These sources explain the synchronization
choice; they do not establish the cause of an uninspected live VM failure.
