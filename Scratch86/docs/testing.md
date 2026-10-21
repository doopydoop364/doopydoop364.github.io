# Testing

Run everything: `make test` (needs `as`/`ld`/`objcopy`, Python venv, `npm ci`).

| Suite | What it proves |
|---|---|
| `tests/cpu/test_acceptance.py` | Spec program (AX=BX=30, flags, EIP, halt); instruction lengths vs Capstone; branches on computed flags; loops; signed vs unsigned jumps; RAM readback; segments + diagnostics; stack/CALL/RET; flag boundaries; 8/16/32-bit register aliasing; unsupported opcode faults; HLT; self-modifying code; prefixes; LOOPNE/LOOPE |
| `tests/memory` | little-endian, unaligned, bank crossing, A20 wrap, HMA, unmapped memory |
| `tests/cpu/test_differential.py` | Random programs (GNU as) run in scratch-vm vs Unicorn: registers, flags, memory windows, EIP. `S86_DIFF_N=3000` for a big run |
| `tests/integration` | Green flag boot, screen cells composed from guest video memory, overlay toggles via menu clicks, pause/step/reset/power |
| `tests/test_build.py` | deterministic build, block-graph validation, only core Scratch opcodes (+pen) |

All guest execution happens in the generated blocks inside the stock `scratch-vm`; Python only prepares inputs and compares outputs. Machine code comes from GNU as; Unicorn is a test oracle only.

Known reference quirk: Unicorn 2.1.4 corrupts CF/AF around LOOPNE, so random tests exclude LOOPNE (covered deterministically).
Set `S86_VM=/path/to/turbowarp/scratch-vm/src/index.js` to run any harness against another VM build (the whole suite except the differential run passed on TurboWarp's scratch-vm 2.1.46 compiler).
