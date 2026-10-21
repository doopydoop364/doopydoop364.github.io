# Architecture

```
tools/sb3_generator/sb3gen.py   Python DSL -> Scratch block graph -> deterministic .sb3   (build time only)
src/scratch/state.py            all variables/lists (Stage, global)
src/memory/memory.py            banked RAM + A20 + accessors
src/cpu/core.py, cpu_handlers.py, control.py   registers, fetch, ModRM/SIB, ALU, flags, handlers, step, reset
src/interface/ui.py, scheduler.py   settings menu, overlays, controls, main loop
src/display/screen.py           Screen sprite: pen-stamped 80x25 text mode, gear sprite
src/scratch/build.py            assembles everything -> builds/Scratch86-v0.1.sb3
```
Python never executes guest code: it only *emits blocks*. Stage = machine; `Screen` sprite = video; `Settings` sprite = gear icon.

Decisions: table-driven dispatch (binary decision tree over opcode ranges); procedures are warp; global scratch temps are partitioned (H1-H4 for handlers, UI_* for the interface); UI composes a transparent 80x25 layer over VRAM so closing an overlay restores the guest screen; requests (`REQ`) are applied only between instructions.
Costumes: 16 background + 16x256 glyph SVGs (4112) for the Screen sprite.
