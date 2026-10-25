# Compatibility intelligence (3.0)

2.0's evidence model (COMPATIBILITY.md) is unchanged and still produces the legacy `status` (Verified Working / Promising / ...). 3.0 adds, on top:

## Separate facts (never merged into one label)
`arch-games compat show GAME` prints them: **installer** architecture (observed or not) · **game executable** (architecture/format/OS, measured from the
launcher you confirmed, else archive/analysis evidence) · **runtime requirements** (runner, DirectX/Vulkan imports, runtime components) · **engine clues**
(filename hints only) · **graphics requirements** · **host capabilities** (architecture, Wine availability/version, renderer, acceleration, display, RAM) ·
**user-confirmed** gameplay and audio, launch and installation status with evidence timestamps · **other machines'** reports (informational only).

## Classification (with reasons)
Verified working · Working with issues · Likely compatible · Requires configuration · Likely incompatible · Unsupported · Unknown.
* Only *your own confirmation on this machine* (`compat set GAME --gameplay works|issues|slow|fails`, or `feedback set`) can produce Verified working / Working with issues.
* A successful install or a launch that started/exited 0 only raises "Unknown" to "Likely compatible"; it never verifies gameplay.
* Missing Wine, no graphical session, no DOSBox, a Wine that cannot run the executable, or an installed game with no registered launcher → Requires configuration.
* PE64 on a 32-bit OS, known resource limits, or your 'slow'/'fails' report → Likely incompatible. Foreign architecture/OS → Unsupported.
* Audio issues you report downgrade Verified working to Working with issues. Audio and gameplay are recorded separately.

## Records and machines
One record per (machine, game) in namespace `compat`: game identity, platform, machine id/name/architecture, Wine version, renderer, installation / launch / audio /
gameplay status, known issues, per-fact evidence timestamps. The machine id is a hash of `/etc/machine-id` (or a locally generated id); the name is detected
(`arch86` = Arch Linux 32, `arch64`, `cachyos`) and can be changed with `machine name NAME`. Another machine's record never verifies a game here.
`compat export` / `compat import FILE` exchange records by file (no network, no credentials, newest update wins, your own machine's records are never overwritten) —
the groundwork for private sync. Records update automatically on install completion/failure, launches (not installer runs) and feedback.

## Example: Undertale on Arch64 (what the user reported)
After `discover undertale --register 1` the launcher's PE32 header is measured (game arch i686, installer arch unknown/separate), the report becomes **Likely compatible**
on x86_64 with a WoW64-capable Wine; after `compat set undertale --gameplay works --audio works` it becomes **Verified working** *on that machine only*; on Arch86 it stays at
its own evidence. These steps have not been performed by the developer: the real Undertale install is on the user's VM.
