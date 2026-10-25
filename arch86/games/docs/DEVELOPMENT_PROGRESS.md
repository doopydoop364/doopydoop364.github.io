# Development progress

Every phase uses a resolvable Git tag as its commit reference. `git rev-parse TAG` gives its SHA. Tests run from arch86/games. Local fixtures do not establish live compatibility.

## Phase 1: Repository audit and architecture

Audited baseline; introduced Python 3.10+ package, shared records, repository filtering, legacy command adapter, representative GameVault fixture, tooling and four baseline tests. Repaired missing-tool crashes. Catalog has 69 records (68 newline characters); tests corrected the initial count assumption.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-01`. Next: phase 2.

## Phase 2: GameVault API integration

Implemented documented GameVault GET login, API keys, token rotation, bounded retries, pagination, details, artwork transport and account-scoped atomic offline cache. Private XDG credential storage; CLI connect/status/list/search/details/disconnect. Eleven tests pass including partial synchronization and invalid authentication.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-02`. Next: phase 3.

## Phase 3: System compatibility detection

Added bounded PE32/PE32+/ELF32/ELF64/DOS header inspection and PE DLL import parsing, kernel/process architecture separation, RAM/disk/runtime detection and conservative OpenGL/Vulkan software-renderer reporting. Missing tools remain unknown; Wine WoW64 is not inferred from version. Fifteen accumulated tests pass.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-03`. Next: phase 4.

## Phase 4: Archive and executable analysis

Implemented isolated libarchive inspection/extraction for ZIP/TAR/TAR.GZ/7z/RAR with CPU, memory, time, count, size and compression limits. Refuses traversal, links, special files, duplicate paths and encrypted/corrupt entries. Original benign 7z/RAR fixtures plus synthetic executable archive tests. Twenty tests pass.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-04`. Next: phase 5.

## Phase 5: Wine compatibility analyzer

Added six structured compatibility statuses, architecture/runtime blockers, Wine WoW64 uncertainty, PE import clues, Vulkan/graphics/RAM assessments and machine-bound feedback gate. No unexplained numerical score or automatic dependency installation. Twenty-five accumulated tests pass across native, DOS, PE32/PE32+, missing runtimes, foreign architecture and weak graphics.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-05`. Next: phase 6.

## Phase 6: Combined game catalog

Unified configured Pacman packages, version-preserving GameVault records and installed local manifests. Search/category/source/status filters explain hidden entries. Deduplication requires explicit canonical identity and matching OS/build; titles alone never collapse editions. Twenty-nine tests pass.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-06`. Next: phase 7.

## Phase 7: Interactive terminal interface

Replaced legacy routing with argparse CLI and combined library integration. Added curses keyboard navigation plus paginated searchable ASCII fallback, all eight sections, details/report views, explicit Pacman confirmation, terminal-control sanitization and narrow-screen rendering. Thirty-two tests pass including fallback navigation and resize snapshots.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-07`. Next: phase 8.

## Phase 8: Visual game gallery

Upgraded localhost read-only gallery with searchable/category/status/architecture-filtered cards, details, local artwork retrieval/cache, status snapshots and polling. Escaped all remote metadata, added CSP/no-sniff/host/origin protections, and rejected action requests. Thirty-four tests pass including real loopback HTTP security tests.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-08`. Next: phase 9.

## Phase 9: Download manager

Implemented persistent authorized download queue, explicit run/retry/cancel/cleanup, streaming progress, locks and crash recovery. Resume validates Range/Content-Range and ETag or Last-Modified; unvalidated partials restart. Includes space/size/byte limits, SHA-256 when supplied, safe filenames and honest verification labels. Thirty-nine tests pass.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-09`. Next: phase 10.

## Phase 10: Game installation system

Added space-checked staged archive extraction, per-game manifests and dependency/executable inventories, transaction-journal recovery, explicit reinstall with retained previous files and recoverable uninstall (optional explicit purge). Archives and Wine state are retained. Forty-two tests pass including failed extraction and interrupted commit recovery.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-10`. Next: phase 11.

## Phase 11: Wine prefix manager

Added explicit win32/win64 isolated prefix creation using installed wineboot, measured registry architecture, health/version/dependency reporting, logs, backups and confirmed removal. Disables Wine mono/gecko bootstrap downloads during creation; no unofficial runtime installers. Forty-five tests pass with mocked Wine, architecture rejection and retained failure diagnostics.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-11`. Next: phase 12.

## Phase 12: Game launcher

Added explicit native/Wine/DOSBox/custom argument-array launches, resolved managed-path confinement with opt-in external override, isolated prefixes and controlled environment customization. Dry runs, private logs, exit/error/cancel history and process-group cancellation. No automatic execution or exit-code-based verification. Fifty tests pass without launching real games.

Review: inspected new and modified source, input validation and backward compatibility.
Validation: full accumulated unittest suite, compileall and git diff --check passed.
Limitations: Live VMs and private GameVault server are not available; fixture results are not hardware verification.
Git commit: `arch-games-2-phase-12`. Next: phase 13.
