# Native-install archive worker failure

## Confirmed cause and reproduction

The original native launcher executes an absolute `app/arch86-games.py` path.
Python consequently adds `app/` to the **parent** interpreter's sys.path. The archive
service then started a second interpreter with `python -m
arch_games.compatibility.archive_worker`, which searches its own working directory
and installed packages, not its parent's sys.path. The native installer copies
modules into app/, rather than site-packages. From an unrelated directory, the
worker therefore exits **1**, before opening an archive or applying resource limits:

```text
Error while finding module specification for 'arch_games.compatibility.archive_worker'
(ModuleNotFoundError: No module named 'arch_games')
```

The parent discarded stderr and converted this to `Archive worker failed or
exceeded resources`. The error needs neither a large archive nor memory pressure.
The previous tests ran from the source directory or used a wheel installation,
which masked the bug. stderr was captured by the parent, so an absence of worker
messages in the user's journal does not establish an OOM kill.

Reproduced before the fix using the existing original tiny stored-method RAR4
fixture and a temporary native application installation, launched from an
unrelated working directory. This reproduced the exact public error and exit 1.
The user's VM and game archive were not accessed; no actual Windows executable
was run. Another native-library failure on the original private archive cannot
be ruled out without rerunning it with the fixed worker diagnostics.

## End-to-end analysis path

1. argparse reads analyze/path/member/game/graphics/debug. Config and SQLite use
   their usual application state, or an isolated --state-dir for tests.
2. inspect_file reads at most 1 MiB to identify a standalone ELF/PE/DOS header.
   An unrecognized archive with a supported suffix goes to inspect_archive.
3. The parent resolves input/destination paths, validates a selected member and
   starts isolated Python (-I -B) with an explicit package root derived from the
   currently executing installation. CWD/PYTHONPATH cannot select another package.
   Only PATH, locale and TMPDIR environment entries are passed; no auth/session
   environment is needed. No shell is involved.
4. The worker applies the unchanged limits: 384 MiB virtual address space,
   30 CPU seconds and a 33-second parent deadline for analysis, 64 descriptors,
   8 GiB per member, 32 GiB total, 10,000 entries and 1000:1 expansion ratio.
   Installation retains its separately configured bounded timeout.
5. System libarchive opens the archive and visits headers. Full inspection keeps
   the existing path/link/special-file/count/size protections. Member inspection
   visits only the necessary preceding headers, reads no other member payload
   explicitly, reads at most a 1 MiB prefix of the target, and stops immediately.
   Sequential/solid formats may require libarchive to skip/decompress preceding
   compressed data internally; the same CPU/memory/decompression bounds apply.
6. PE parsing checks MZ/e_lfanew, PE signature, machine, optional-header size/magic,
   DLL import tables within the prefix, and subsystem. PE32/i386/Windows GUI is
   reported for the synthetic setup fixture. No target is executed. Analysis
   creates no extracted game files or temporary game payloads; libarchive discovery
   may use ordinary system-library lookup internals.
7. The worker emits bounded JSON. Known failures have typed errors; unexpected
   Python failures emit a safe type plus a captured traceback. Native errors retain
   libarchive stage/errno/error-string details for opt-in diagnostics.
8. The parent distinguishes startup/exit, crash signals, CPU/file-size limits,
   timeout, address-space exhaustion, corrupt input, unsupported format/features,
   unsafe paths, missing member, I/O and parsing/protocol errors. SIGKILL remains
   explicitly ambiguous: OOM or an external kill is possible, not proven.
9. CLI --debug (before analyze or after its options) prints bounded diagnostics,
   redacting credential-file values, sensitive environment values, auth headers,
   URL userinfo and common secret assignments, without dumping locals/configuration.
10. With --game, successful selected evidence is bound to the source archive by a
    streaming SHA-256 read and stored transactionally. Hashing reads the archive's
    bytes but does not enumerate or extract other members. Failed analysis leaves
    existing evidence intact.

## Installer evidence scope

Recognizable setup/install filenames and bounded Inno Setup markers are labeled
installer hints; they are not definitive identification of every possible installer.
The selected PE architecture belongs to that executable only. It does not establish
the architecture of files created by running the installer. Reports explicitly show
installer scope, unknown game architecture and unknown game compatibility.

`analysis[GAME_ID].installer_analysis` stores installer observations separately.
Existing game executable evidence and requirements are preserved. Library reports
also handle older records/manifests whose selected filename was setup_*.exe, and
will not promote a successful installer launch into verified gameplay. Reanalyzing
an actual game executable remains a separate user action.

## Regression data and review

Tests generate original RAR v4 stored blocks: directory, unrelated executable-name
member, setup_example_game_1.08_(18328).exe with nonfunctional synthetic PE32 GUI
headers/Inno marker, and a separate PE64 header. Larger unrelated-prefix and unsafe
trailing-entry variants verify selective reading and early stop. This reproduces
the relevant reported archive layout and installation/import context, not the
private archive's compression stream or game contents. No real game data is stored.

Coverage includes all native aliases from an unrelated/spoofed CWD/PYTHONPATH,
RAR listing/member analysis, gamevault:32 persistence, preserved existing evidence,
legacy installer records, failure preservation, native staged extraction, PE32 GUI
classification, worker status/signal/timeout/protocol errors, Python/MemoryError
error envelopes, debug redaction and unchanged resource limits.

Security/correctness/data-loss review: isolated startup uses the trusted installed
package; no limits were increased, unsafe full extraction is still refused,
member-only results explicitly state incomplete listing/no complete validation,
no installer is executed, no source archive is removed, and no schema migration
or deletion of existing evidence/configuration is needed. Full existing tests,
new regressions, Python 3.10/3.14, lint, formatting, compilation and shell syntax
checks are run before committing. Actual private-archive/VM confirmation remains
an external validation step.

Final local result: 88/88 tests on Python 3.14.8 and 88/88 on Python 3.10.20
(all 77 previous cases plus 11 new cases), with native install/member analysis/
staged extraction exercised from an unrelated working directory. Lint, formatting,
compilation, shell syntax and diff checks passed.
