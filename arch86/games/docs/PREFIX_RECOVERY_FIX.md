# Wine prefix synchronization and recovery

## Findings

The previous create flow ran wineboot -u, checked system.reg/user.reg/drive_c once,
and permanently saved failed if that one check failed. Later prefix check could
report healthy, yet launch still rejected its failed database state. There was no
recovery action; earlier troubleshooting incorrectly suggested removing the prefix.

A controlled real-subprocess fixture reproduced this without relying on an
arbitrary sleep: a Python wineboot stand-in exited zero after spawning a child;
the child waited for a handshake before writing realistic Wine registry files.
The old manager failed immediately; after deferred persistence, it reported
healthy files while the database remained failed. The new manager waited for the
prefix-scoped server stand-in, then succeeded. No real Wine executable or Windows
game/installer was run by this fixture.

Before-fix reproduction output:

```text
BEFORE FIX: Wine did not create a complete prefix; inspect local runtime log
AFTER DEFERRED WRITE: healthy=True, database=failed
FIXED INITIALIZATION: ready; wineboot_exit=0; wineserver_exit=0
RECOVERY OF OLD PREFIX: ready; original instance retained=True
```

Wine 11.20's [wineboot source](https://github.com/wine-mirror/wine/blob/wine-11.20/programs/wineboot/wineboot.c)
signals its boot event and exits; the [registry implementation](https://github.com/wine-mirror/wine/blob/wine-11.20/server/registry.c)
includes deferred saving. The [server wait option](https://github.com/wine-mirror/wine/blob/wine-11.20/server/main.c)
waits for the current server to terminate. These primary sources support waiting
for prefix-specific persistence rather than assuming wineboot exit means files
are already on disk. They do not prove the cause of the user's particular VM run.

The reported old error identifies a failed structural check at that moment, not
a diagnosis from Mesa/OLE log messages. The old implementation did not record
which path was missing or why. Other causes such as permission errors or an
unexpected prefix path cannot be excluded without inspecting that VM.

## New initialization

- Require the installed wine, wineboot and wineserver for a new prefix; never
  install them automatically.
- Persist creating state and log location before running Wine. Run wineboot once,
  then wineserver -w with exactly the same isolated WINEPREFIX/WINEARCH environment.
- Wait at most 30 seconds for that prefix's server, within a total initialization
  deadline (default 120 seconds after version detection; configurable 1..600).
  Do not kill the server or any remaining processes on timeout.
- After successful synchronization, poll structural health for at most five seconds
  within the remaining deadline. Require two stable registry observations.
- Validate regular, readable registry files, Wine registry headers, consistent
  architecture matching the saved request, and a drive_c directory. Wine's normal
  relocated-registry/drive-directory symlinks remain supported; their targets
  are not modified by verification/recovery.
- Record command exit statuses, stage, verification attempts, failure type/errno,
  timestamp and concrete file issues. Log warning strings alone do not decide
  health; genuine nonzero exits and timeouts remain visible failures.
- Preserve the prefix, log and creating/failed record after failure/interruption.
  An audit-event error after publishing ready cannot incorrectly revert it to failed.

The overall deadline and per-stage bounds prevent indefinite waits, including
persistent servers. A timeout can leave a prefix becoming healthy later; use the
recovery path instead of deleting it.

## Recovery and preservation

```sh
arch-games prefix check gamevault:32
arch-games prefix recover gamevault:32
```

Check reports structural health separately from saved lifecycle state. It offers
recovery when the files are healthy but the record is failed/creating. Recovery
waits for stable healthy files (default five seconds; --timeout up to 120), runs no
Wine command, and updates only managed Arch Games metadata and audit records.
It retains the prefix directory, registry/save/game files and timestamps,
instance ID, environment, original Wine creation version, log, failure details
and extra application metadata. It does not rebuild or delete anything.

Repeating create with the same architecture also recovers an already healthy
failed/creating record, without rerunning wineboot. Architecture changes,
unmanaged directories, invalid registries and inconsistent architectures are
refused while leaving existing data intact. Launch directs healthy stale records
to recovery; a dry-run does not silently update their state.

Structural ready is not a claim about actual Wine functionality, graphics support,
Windows dependencies or gameplay. A genuinely failed bootstrap may require further
user-directed diagnosis even when a structural recovery succeeds. No game result
is verified automatically.

## Live access limitation

Arch64 was visible as an online Tailscale peer. SSH by hostname refused the unknown
hostname key; an existing trusted key for the observed IP allowed strict identity
checking, but authentication was denied. No host-key check was disabled, alternate
accounts/passwords were guessed, or remote commands executed. Thus the existing
live prefix, registry timestamps, process state and full initialization log could
not be inspected. The exact cause on that VM remains unconfirmed. No live VM
configuration was changed.

## Tests and review

The complete suite passes 103/103 on Python 3.14.8 and 103/103 on Python 3.10.20.
This includes the previous 88 cases and 15 new lifecycle regressions: real child
process handshake, per-prefix command ordering, delayed file polling, bounded
timeouts/deadlines, stale failed/creating state, read-only checks, metadata/data
preservation, normal Wine symlinks, invalid files/architecture, nonzero bootstrap,
launcher gating, CLI recovery without Wine, unmanaged directories and audit errors.
Existing immediate-completion runtime fixtures now use realistic registry headers
and model wineserver separately. Ruff, formatting, compilation, shell syntax and
diff checks are run before committing. The actual Wine 11.20 VM behavior still
requires user-side acceptance with the updated diagnostic/recovery commands.
