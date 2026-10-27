# Testing

Run everything: `make test` (needs `as`/`ld`/`objcopy`, Python venv, `npm ci`).

| Suite | What it proves |
|---|---|
| `tests/cpu/test_acceptance.py` | Spec program (AX=BX=30, flags, EIP, halt); instruction lengths vs Capstone; branches on computed flags; loops; signed vs unsigned jumps; RAM readback; segments + diagnostics; stack/CALL/RET; flag boundaries; 8/16/32-bit register aliasing; unsupported opcode faults; HLT; self-modifying code; prefixes; LOOPNE/LOOPE |
| `tests/cpu/test_v02.py` | Directed v0.2 tests with Python-computed expectations: 64-bit MUL/IMUL/DIV/IDIV extremes and #DE, IVT delivery/IRET/INTO/BOUND, REP restart and DF, shift/rotate edges, segment limits, TF, far transfers, 0F opcodes, I/O stubs |
| `tests/cpu/test_pm.py` | Directed protected-mode tests (expectations from the SDM): mode switch, 16/32-bit code, descriptor-load rules and error codes, null/not-present/read-only/execute-only/expand-down/granularity, far transfers, LAR/LSL/VERR/VERW, LLDT/LTR, SGDT/LGDT, CR0/LMSW/CLTS, unreal mode, ARPL |
| `tests/cpu/test_pm_int.py` | Protected-mode interrupt/privilege tests with real IDT gates, TSS, ring-3 code: interrupt vs trap gates, error codes, #DE, gate-DPL and not-present gates, ring3->ring0 stack switch and IRET back, data-segment nulling, privileged instructions at CPL 3, I/O bitmap, POPF masking, #DF, triple fault, RETF to outer level |
| `tests/memory` | little-endian, unaligned, bank crossing, A20 wrap, HMA, unmapped memory |
| `tests/cpu/test_differential.py` | Random programs (GNU as) run in scratch-vm vs Unicorn: registers, flags, memory windows, EIP. `S86_DIFF_N=3000` for a big run |
| `tests/integration` | Green flag boot, screen cells composed from guest video memory, overlay toggles via menu clicks, pause/step/reset/power |
| `tests/test_build.py` | deterministic build, block-graph validation, only core Scratch opcodes (+pen) |

All guest execution happens in the generated blocks inside the stock `scratch-vm`; Python only prepares inputs and compares outputs. Machine code comes from GNU as; Unicorn is a test oracle only.

Known reference quirk: Unicorn 2.1.4 corrupts CF/AF around LOOPNE, so random tests exclude LOOPNE (covered deterministically).
Set `S86_VM=/path/to/turbowarp/scratch-vm/src/index.js` to run any harness against another VM build (the whole suite except the differential run passed on TurboWarp's scratch-vm 2.1.46 compiler).

## Differential oracle policy (v0.2)
* Programs are 1-5 fully defined instructions plus at most one final "special" instruction (MUL/DIV/shift/BT/BCD/string/...). The generator records the flags that special instruction leaves architecturally undefined and the comparison masks exactly those, so undefined values can never feed later instructions.
* Exceptions: Unicorn's interrupt hook supplies the vector and the EIP that real hardware would save; Scratch86 must stop with the same vector and EIP and unchanged registers/memory (with no IVT handler it stops visibly instead of vectoring). The IVT delivery path is tested directly in `test_v02.py`.
* Known oracle deviations (each verified, none hidden): Unicorn 2.1.4 corrupts CF/AF/OF around LOOPE and LOOPNE (excluded from random tests; deterministic tests cover them); it reports stale arithmetic flags when BOUND raises #BR (flags masked for that case); it does not check real-mode segment limits, so Scratch86 #GP/#SS on a 32-bit effective address above 0xFFFF is counted as "skipped", not compared (directed tests cover the limit logic).
* Reproduced baseline on clean v0.1 HEAD, seeds 10000-12999: 3000 programs, 2689 comparable, 5 mismatches, all `loope` + Unicorn flag quirk. v0.2 generator, seeds 2400-5399: 3000 programs, 0 mismatches, 430 skipped as above.
* Protected-mode differential: `tests/pmprog.py` wraps the same random programs in a real-mode bootstrap (LGDT, PE, far jump to a 16-bit code segment, DS/ES/SS descriptors reproducing the real-mode windows) executed by both engines. 2000 programs: 0 mismatches (267 skipped as limit-class). Real mode re-run after the v0.3 changes: 2000 programs, 0 mismatches.
* The comparison treats any Scratch86 #GP/#SS that the oracle did not raise as "limit class" (QEMU never checks data limits); a vector that both raise is compared.
