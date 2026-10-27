# Scratch86

An x86 PC emulator whose runtime is **100% native Scratch blocks** (TurboWarp recommended). Python/Node/GNU tools exist only at development time: they generate the block graph, assemble test programs, and run tests. They never execute guest instructions.

**Status: v0.1 release artifact; development branch is mid-way through v0.3-v0.4 (versions are earned by acceptance criteria, see `docs/roadmap.md`).** The CPU now implements the real-mode integer instruction set (MUL/DIV, shifts, strings with restartable REP, INT/IRET, far transfers, BCD, the 0F integer opcodes), 386 protected mode (segmentation, privilege levels, IDT gates, TSS stack switching, IRET/RETF to outer levels, I/O permission bitmap, double/triple fault) and two-level paging with a TLB. Still missing: call/task gates and task switching, virtual-8086 mode, CPU-profile gating, x87, devices, BIOS, disks, input - so it does not boot an operating system yet. Details and test evidence: `docs/DEVELOPMENT_PROGRESS.md`, `docs/cpu.md`, `docs/testing.md`.

Artifact: `builds/Scratch86-v0.1.sb3` (open in TurboWarp or Scratch). Green flag boots a demo written in x86 assembly (`firmware/demo.asm`) that draws text and colour bars and runs a live hex counter. Click the small gear (top-right) for controls (power, reset, pause/resume, step) and 21 diagnostic overlays (all off by default).

## Build and test
Requirements: Python 3, Node, GNU binutils (`as`, `ld`, `objcopy`).
```
make setup      # venv + npm ci
make build      # builds builds/Scratch86-v0.1.sb3 (deterministic)
make validate
make test       # ~12 min (101 tests incl. differential runs against Unicorn)
make bench
```
Docs: `docs/architecture.md`, `cpu.md`, `memory.md`, `testing.md`, `benchmarks.md`, `roadmap.md`, `DEVELOPMENT_STATUS.md`.

Performance (measured, headless node): stock scratch-vm ~34k instr/s; TurboWarp compiler 1.2-3.3M instr/s. Details and caveats in `docs/benchmarks.md`.
