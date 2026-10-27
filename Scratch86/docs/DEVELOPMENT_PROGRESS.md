# Development progress (resume file)

Branch `scratch86-development`. Stage only `Scratch86/` and `.github/workflows/scratch86.yml`.
The remote repo also holds unrelated branches; `git fetch` before push, plain push only.
Large OS images and product keys live in `media/` (git-ignored). Scratch/log files go in `builds/tmp/` (git-ignored).

## Current state (2026-10-26)
* **v0.1 verified** (31 tests, 183 s). Baseline differential reproduced on a clean worktree: seeds 10000-12999, 3000 programs, 2689 comparable, 5 mismatches, all LOOPE + Unicorn flag quirk (see docs/testing.md).
* **v0.2 (real-mode CPU) - implemented, verified by 3000-seed differential (0 mismatches) + 24 directed tests + full suite**; still to do before the v0.2 label is earned: see "v0.2 gaps".
* Exceptions: pending-fault convention + real-mode IVT delivery + TF trap done. Segment-limit checks done for r/m, string, XLAT, BOUND, far-pointer accesses (not yet for stack pushes/pops, moffs, instruction fetch).
* OS Palooza drive present but **unmounted** -> docs/os-media-inventory.md (needs user approval to mount read-only).

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

## Next tasks
1. Commit/push v0.2 slice; run big differential (S86_DIFF_N=3000) in CI-equivalent form.
2. v0.3: CPU profile variable, CR0/CR2/CR3/CR4-less control registers, hidden segment descriptor cache (base, limit, attributes), GDT/LDT/IDT, protected-mode segment loads with checks, far transfers/gates, privilege and TSS stack switch, protected-mode IDT delivery with error codes, system instructions (LGDT LIDT LLDT LTR LMSW SMSW MOV CRn CLTS ARPL LAR LSL VERR VERW INVLPG), then #GP/#SS/#NP/#PF/#DF semantics.
3. Test strategy for v0.3: Unicorn supports protected mode (UC_MODE_32) with descriptor tables set up by the test; build a protected-mode program generator + directed privilege tests.

## Blockers
* OS Palooza unmounted (needs user approval).
