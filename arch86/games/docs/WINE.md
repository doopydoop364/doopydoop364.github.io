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
