# Compatibility methodology

No single numerical score is used. Reports contain status/confidence,
architecture match, runtime, graphics, memory, missing dependencies, supporting
evidence, remaining uncertainty and a stable machine fingerprint.

| Status | Meaning |
|---|---|
| Verified Working | A local user attested works/works with issues for the recorded machine/configuration. Never inferred from exit code. |
| Promising | Evidence supports an attempt; no local gameplay confirmation. |
| Experimental | Significant uncertainty, such as unknown WoW64 or graphics requirements. |
| Unlikely | Known RAM or graphics limitations make reasonable gameplay doubtful. |
| Unsupported | A specific blocker exists for the selected method, e.g. PE64 on i686, missing Wine, no required display or Vulkan. This can be recoverable after setup. |
| Unknown | Not enough architecture/platform evidence for a meaningful prediction. |

Header readers recognize x86 PE32/PE32+, ELF32/ELF64 and MZ DOS; foreign machine IDs
are retained and rejected by unsupported launch methods. Ambiguous/truncated MZ,
NE/LE, COM files without reliable headers, scripts and packed formats stay unknown.
ELF class/machine is measured; Linux ABI is an explicit assumption, with native
loader/library caveats. Binaries are never executed to classify them. PE import
names can indicate DirectX/OpenGL/Vulkan or runtime components, but imports do
not establish minimum API versions or actual rendering paths.

Kernel architecture, Python bitness, /proc/meminfo and filesystem free space are
measured independently. Graphics tools are optional, timeout-bounded system
programs. llvmpipe/lavapipe/software renderers produce conservative results.
Missing measurements remain null/unknown, not false claims of support. A display
is needed for graphical games; category Terminal or an explicit --terminal
assertion removes that requirement. CPU instruction-set requirements remain
unknown without game-specific evidence.

Analyze a local executable: `arch-games analyze ./Game.exe`. Analyze an archive:
`arch-games analyze ./Game.zip`; to save one executable as evidence use
`--game 'gamevault:12@34' --member 'Game/Game.exe'`. User-supplied requirements:
`--graphics 2d|3d|unknown`, `--ram-mb N`, `--vulkan`, `--terminal`. Such flags are
user assertions, not server facts. Analysis stores the source digest and origin.
For multiple launchers, choose a member explicitly rather than assuming the first.

Feedback accepts works/issues/slow/fails/untested plus notes and optional launch ID.
Successful results normally require a completed local launch. `--manual` explicitly
records the user's independent attestation, not an automated observation.
Architecture, Wine, renderer, memory total, installation transaction, executable
file identity and launch configuration bind the evidence. A new installation,
modified executable or subsequent launch invalidates prior effective feedback.
Historical records stay local. Results never upload automatically.
