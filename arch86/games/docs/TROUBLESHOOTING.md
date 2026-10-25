# Troubleshooting and target VMs

**Arch86:** Linux i686/Pentium4 kernel, 2 GiB RAM, 2 vCPUs, basic QEMU graphics.
Use configured Arch Linux 32 repositories. Keep memory-heavy 3D expectations low.
PE32 requires available Wine support; PE32+ is unsupported by the 32-bit OS.

**Arch64:** Linux x86-64 kernel, 4 GiB RAM, 4 vCPUs, QEMU graphics. Modern Wine
WoW64 may run PE32 in a win64 prefix; win32 prefix support is not assumed. Basic
virtual display hardware is not a promise of 3D acceleration. Both machines use
one Python 3.10+ codebase; 32-bit behavior is fixture-tested unless stated in
VALIDATION.md. Real VM acceptance remains required.

**No Pacman entries:** check configured sync databases and network using your
normal full-system Arch maintenance procedure. Arch Games never refreshes package
databases. Do not perform a standalone partial-upgrade workflow solely to browse.
Missing Pacman is an actionable notice on a non-Arch development host.

**GameVault offline/401:** check URL and private network reachability, consult
server docs, reconnect for expired credentials, or use `gamevault list --offline`.
A cache is scoped to server/account and needs one complete successful sync first.
No credentials? Pacman browsing and local game management remain usable.

**Missing artwork:** covers need valid server metadata, auth and supported image
MIME types. The generated-style category cover remains usable. Restart the gallery
after changing server settings or library metadata.

**Unknown compatibility:** architecture/requirements are absent from the public API.
Use an already-local archive or binary; select an explicit member when saving
archive evidence. `list --all` and `reports` expose every prediction and uncertainty.
Do not download a large game solely because its title resembles a lightweight one.

**Archive failure:** check libarchive, corruption, encoding, encryption, path/link
policy and limits. Large solid archives can hit inspection time budgets. Install
allows `--timeout N` (1..3600 seconds); extraction and inspection are bounded.
Nested archives need a separate explicit inspection/extraction workflow. Never
turn off path/link checks to make an archive install.

**Download failed:** use `download list`, then `download retry ID` and `download run`.
Use `retry ID --restart` if the server changed or the partial file is inconsistent.
`cancel ID --cleanup` removes a partial after the active worker stops. Check free
space (64 MiB reserve), metadata size and expected digest. There is no checksum
claim when only bytes or size are checked.

**Install interrupted:** rerun the install command; the journal completes a committed
rename or rolls back incomplete staging. Previous game versions are retained under
backups/. Uninstall without --purge retains a backup and does not reclaim its space.
Application upgrades have their own recovery journal and app.previous directory.

**Wine prefix failed:** `prefix check ID` and local logs identify a missing runtime,
unsupported prefix architecture or incomplete registry. Remove the failed prefix
explicitly before retrying. No DLL helper or privileged package installation runs.

**Launch failed:** first use --dry-run, inspect the executable member and selected
prefix. `history` shows exit status and a private log. The launcher does not resolve
arbitrary native library dependencies for you. KMSCON can browse without Xorg;
graphical games still need i3/Xorg or another display session. Stop foreground
operations with Ctrl+C. Wine child processes may require normal Wine cleanup.
