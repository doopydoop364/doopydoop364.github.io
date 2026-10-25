# Validation matrix and acceptance checklist

Automated results are detailed in DEVELOPMENT_PROGRESS.md. The development host
is Linux x86-64; the full suite runs on Python 3.10.20 and 3.14.8. Tests emulate i686/Pentium4 and x86-64 profiles,
generate synthetic ELF/PE/DOS headers and inspect benign ZIP/TAR/TAR.GZ/7z/RAR
archives. Loopback HTTP tests cover the gallery, and network doubles cover
GameVault and downloads. A real loopback GameVault fixture exercises auth, pagination, range downloads, archive install, mocked prefixes/launch, feedback and CLI for both architecture profiles. These results do not establish actual gameplay support.

| Scenario | Arch86 fixture coverage | Arch64 fixture coverage | Live VM |
|---|---|---|---|
| Configured Pacman catalog | Included | Included | Pending |
| Architecture detection | Included | Included | Pending |
| GameVault browse/cache/auth | Included (architecture independent) | Included | Pending |
| Executable/archive analysis | Included | Included | Pending |
| Wine reports and prefix handling | Mocked runtime | Mocked runtime/WoW64 | Pending |
| Terminal UI, narrow/ASCII fallback | Included | Included | Pending |
| Localhost gallery | Included | Included | Pending |
| Download queue/resume/cancel/verify | Included | Included | Pending |
| Game/app installer and recovery | Included | Included | Pending |
| Native/Wine/DOSBox launch plans | Generated commands, no games | Generated commands, no games | Pending |

Before deployment, use a snapshot of each VM and a small authorized sample game:

1. Install as the ordinary user; run doctor/info and all three aliases.
2. Compare Pacman catalog entries with local -Slq/-Qq databases; test one confirmed
   package install using Pacman's normal interactive safeguards.
3. Exercise the menu inside KMSCON, SSH and tmux; resize, search, navigate and quit.
4. Confirm actual display/renderer/OpenGL/Vulkan and Wine prefix architecture.
   Inspect a known PE32 game on both VMs; confirm PE32+ is blocked on Arch86.
5. Compare the private server's schema, connect, sync pages/details/artwork, then
   disconnect network and verify cached labels/timestamps and credential privacy.
6. Queue a small authorized archive, interrupt/resume, cancel/cleanup and verify
   the exact bytes. Check server range/validator behavior before large downloads.
7. Install it, inspect the manifest, confirm nothing launches, test reinstall
   rollback and backup/uninstall behavior. Verify user saves/configuration remain.
8. Create the appropriate isolated prefix explicitly, inspect its log/registry,
   and review a launch dry-run. Launch explicitly and assess actual gameplay.
9. Record works/issues/slow/fails locally; change the prefix or installation and
   confirm the older result no longer establishes current verification.
10. Exercise the gallery only on localhost, then perform an application upgrade
    and uninstall in the VM snapshot. Restore the snapshot if acceptance fails.

Unverified external dependencies: private GameVault version/auth, private network,
real Pacman transactions, 32-bit Python ABI and libarchive, display stacks and Wine,
real games and practical graphics performance. They are acceptance prerequisites,
not features claimed to have passed a mocked hardware test.

Hosted CI passed the Python 3.10/3.14 matrix for the complete phase-17 implementation:
https://github.com/doopydoop364/doopydoop364.github.io/actions/runs/40227423391 .
Local final runs: 77/77 tests on Python 3.10.20 and 77/77 on Python 3.14.8.
Ruff, formatting, compileall, shell syntax, installed wheel/aliases/catalog and
browser search/details checks passed. No actual game execution was used.
