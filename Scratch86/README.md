# Scratch86

An x86 PC emulator whose runtime is **100% native Scratch blocks** (TurboWarp recommended). Python/Node/GNU tools exist only at development time: they generate the block graph, assemble test programs, and run tests. They never execute guest instructions.

**Status: v0.1 - CPU foundation.** A real-mode x86 core (8/16/32-bit operands, ModR/M + SIB, ~100 opcodes - see `docs/cpu.md`) fetches, decodes and executes machine code from emulated RAM, with a colour text screen read from guest video memory. It does **not** boot DOS or any OS yet: no BIOS, interrupts, devices, disks or input. It is not an 80386.

Artifact: `builds/Scratch86-v0.1.sb3` (open in TurboWarp or Scratch). Green flag boots a demo written in x86 assembly (`firmware/demo.asm`) that draws text and colour bars and runs a live hex counter. Click the small gear (top-right) for controls (power, reset, pause/resume, step) and 21 diagnostic overlays (all off by default).

## Build and test
Requirements: Python 3, Node, GNU binutils (`as`, `ld`, `objcopy`).
```
make setup      # venv + npm ci
make build      # builds builds/Scratch86-v0.1.sb3 (deterministic)
make validate
make test       # ~3 min
make bench
```
Docs: `docs/architecture.md`, `cpu.md`, `memory.md`, `testing.md`, `benchmarks.md`, `roadmap.md`, `DEVELOPMENT_STATUS.md`.

Performance (measured, headless node): stock scratch-vm ~34k instr/s; TurboWarp compiler 1.2-3.3M instr/s. Details and caveats in `docs/benchmarks.md`.
