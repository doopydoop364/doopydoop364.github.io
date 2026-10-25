# Wine in 3.0

Per-game isolated prefixes (unchanged), plus runtime awareness, diagnostics and per-game configuration. Arch Games never downloads Wine builds, never installs DLL packages
or winetricks verbs, and Proton is not required (register a Proton-shipped or other `wine` binary yourself with `wine register-runtime`; only its layout/version are inspected).

## Architecture rules
* The prefix architecture is chosen from the **host** and Wine's capability, never from the installer's or game's bitness. x86_64 → `win64` (modern WoW64 runs 32-bit programs too);
  i686 → `win32`. Override per install with `--prefix-arch` when you know better; unsupported combinations are refused with the reason.
* Installer architecture and installed-game architecture are recorded as separate facts. The registered launcher's executable is what the compatibility report measures.
* Runtime layout is classified from the build's library directories: `wow64` (x86_64-unix + i386-windows), `multilib` (both unix sets), `win64-only`, `win32-only`, or `unknown`; this is file-layout evidence, not an execution probe.

## Commands
`wine doctor [--json]` · `wine runtimes` · `wine register-runtime NAME /abs/path/wine` · `wine status GAME` · `wine logs GAME [--lines N] [--raw]` (redacted by default: home, user name, IPs,
tailnet names, bearer tokens, `password=`) · `wine config GAME [--runtime N] [--windows-version win7|...] [--dll-override DLL=n,b] [--env WINEESYNC=1] [--virtual-desktop 1280x720|off] [--debug -all|off] [--clear]`
· `wine apply-version GAME` (runs `winecfg -v` in that prefix) · `prefix list|create|check|recover|backup|backups|restore|inspect|export|remove`.
Per-game settings apply on the next launch (`WINEDLLOVERRIDES`, `WINEDEBUG`, allowlisted env prefixes WINE*/DXVK*/VKD3D*/MESA*/…, `explorer /desktop=` for virtual desktops, runtime binary).

## Prefix safety
* `prefix recover` is the 2.0 mechanism, untouched: `wineserver -w`, bounded verification, metadata-only repair; a prefix marked failed but with valid registry files is recovered without touching its data.
* `prefix restore GAME BACKUP` previews by default; `--apply` replaces the prefix, keeps the old one as a backup and rolls back if the restored prefix fails its health check. A save backup is taken first when save folders are registered.
* Existing prefixes (e.g. Undertale's `gamevault:32`) are recognised by ID: `arch-games prefix check gamevault:32` — not modified.

## Known limits
Missing-DLL detection is a hint (imports not found beside the game or in the prefix's system dirs; many are Wine built-ins). `apply-version`, DLL overrides and virtual desktops have been
unit/fixture tested only; they have not been exercised against a real Wine build here.
