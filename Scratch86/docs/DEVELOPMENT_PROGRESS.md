# Development progress (resume file)

Branch `scratch86-development`. Stage only `Scratch86/` and `.github/workflows/scratch86.yml`.
The remote repo also holds unrelated branches; `git fetch` before push, plain push only.
Large OS images and product keys live in `media/` (git-ignored). Scratch/log files go in `builds/tmp/` (git-ignored).

## Current state (2026-10-26)
* **v0.1 verified** (31 tests, 183 s). Baseline differential reproduced on a clean worktree: seeds 10000-12999, 3000 programs, 2689 comparable, 5 mismatches, all LOOPE + Unicorn flag quirk (see docs/testing.md).
* **v0.2 (real-mode CPU) - implemented, verified by 3000-seed differential (0 mismatches) + 24 directed tests + full suite**; still to do before the v0.2 label is earned: see "v0.2 gaps".
* Exceptions: pending-fault convention + real-mode IVT delivery + TF trap done. Segment-limit checks done for r/m, string, XLAT, BOUND, far-pointer accesses (not yet for stack pushes/pops, moffs, instruction fetch).
* OS Palooza drive present but **unmounted** -> docs/os-media-inventory.md (needs user approval to mount read-only).

* **v0.3 slices A+B done**: 36 directed protected-mode tests, 2000-program PM differential (0 mismatches), real-mode differential re-run (0 mismatches).

## Decisions
* Fault convention: a faulting helper calls `cpu_fault` and `stop_script`s (returns from the helper); handlers call `fchk` after rm_get/rm_set etc. and before modifying state; `cpu_step` delivers after the handler (control.py `exc_deliver`).
* REP: one iteration per step, EIP rewound to the prefix start (restartable, interruptible).
* 64-bit intermediates: 16-bit limbs (cpu_math.py `umul`, `udivp`); IDIV/IMUL work on magnitudes.
* Two-byte opcodes: OPCODE = 0x100 + second byte; dispatch in `h_0f`.
* Undefined flags: documented in docs/cpu.md; oracle comparison masks exactly the flags the final special instruction leaves undefined.
* Unset IVT entry (0000:0000) stops the CPU with the fault banner (diagnostic, switchable) so that running without a BIOS is debuggable.

## v0.2 gaps (backlog, highest first)
1. Segment-limit checks for stack accesses, moffs (A0-A3), instruction fetch past 0xFFFF; stack-fault (#SS) behaviour at SP wrap.
2. CPU-profile gating (8086/286/386/486 opcode and flag differences) - needs a profile variable; part of v0.3.
3. Randomized coverage for: far transfers, IRET/IVT delivery (directed tests only), ENTER nesting >0, 32-bit address size in string ops beyond smoke level, BT with register offset on 32-bit memory.
4. POP SS / MOV SS interrupt shadow (needs hardware IRQ delivery, v0.5).
5. LOCK prefix validity (#UD on non-lockable), 0F 1F NOP hints, SALC, ICEBP.
6. Visual verification of the renderer in TurboWarp; browser benchmark (docs/DEVELOPMENT_STATUS.md "Not verified").

## v0.3 status
Slice A (segmentation, system instructions) and slice B (IDT delivery, privilege transitions, IRET/RETF outer, I/O bitmap,
#DF/triple fault) are implemented and tested (see docs/cpu.md). Remaining for the v0.3 acceptance criteria:
1. Call gates, task gates and TSS task switching (far CALL/JMP to TSS, INT through task gate, IRET with NT, #DF task gate) - currently vector 256.
2. Virtual-8086 mode (VM flag, IRET to V86, V86 interrupt redirection through the IVT/IOPL, #GP monitor traps).
3. CPU profiles (8086/286/386/486 gating of opcodes and flag behaviour) - `PROFILE` variable exists, only CR0 write mask and INVD/INVLPG use it.
4. Debug registers (MOV DRn currently vector 256), alignment check (#AC), #NM/#MF placeholders for the FPU.
5. Randomised protected-mode privilege tests against Unicorn (needs ring-3 execution in the oracle); today privilege logic is covered by directed tests only.

## Next tasks (highest first)
1. v0.3 remaining items above (1-2 matter for Windows 9x/NT; call gates for Win9x thunks, task gates for #DF).
2. v0.4: paging (see plan below), then RAM sizes 2-128 MiB, ROM/MMIO regions.
3. v0.5 hardware (PIC/PIT/CMOS/8042/FDC/IDE/VGA) and v0.6 BIOS.

### Paging plan (v0.4)
* Translation inside the memory procs (`_xlate` in memory.py): when CR0.PG, linear -> physical via a direct-mapped TLB (list-based), page walk on miss with A/D bit updates, U/S and R/W checks (CR0.WP for supervisor writes), #PF with CR2 + error code (P, W/R, U/S, RSVD, I/D).
* Page-crossing accesses fall back to byte-wise paths after probing both pages (no partial writes).
* Every caller must check `FAULT_PEND` after memory access: rm_get/rm_set already do (fchk). To add: fchk after every `pop`, `fetch8`, `fetch_imm`, `modrm_decode` call in the handlers (regex pass), and a fetch-fault check in the prefix loop of cpu_step.
* RMW instructions need a write-probe before any flag change (use a `rm_get_w` variant) so a restarted instruction sees the original flags.
* INVLPG, MOV CR3, MOV CR0 (PG/WP changes) flush the TLB.

## Blockers
* OS Palooza unmounted (needs user approval).
