# Development status

> Superseded for day-to-day tracking by `docs/DEVELOPMENT_PROGRESS.md`. This file describes the v0.1 release snapshot.

**Version:** 0.1.0 (CPU foundation). **Milestone:** v0.1 complete except items under "Not verified".

## Completed
Generator (sb3gen DSL, deterministic .sb3), banked 1 MiB+HMA memory with A20, real-mode x86 core (see docs/cpu.md for the opcode list), ModR/M with 16-bit and 32-bit/SIB addressing, flag-accurate ALU for 8/16/32 bits, stack, branches, loops, prefixes, explicit fault on unsupported opcodes, 80x25 colour text screen from guest video memory, settings gear + menu, 21 diagnostic toggles (default off), power/reset/pause/resume/step, demo boot image (real x86 code writing text), tests, validators, CI workflow, benchmarks.

## Tests
`make test`: 31 passed (acceptance 15, memory 7, differential 2, interface 4, build 3). Large differential run: 3000 random programs, 2719 comparable, 0 mismatches apart from 8 LOOPNE cases traced to a Unicorn 2.1.4 flag bug (LOOPNE now excluded from random tests, verified deterministically). A second 3000-seed run was started but its result was not reviewed.
Known failing tests: none.

## Not verified / limitations
* Pen-stamped rendering, glyph alignment and the gear/menu clicks have only been verified at the data level (composed-cell lists in headless scratch-vm). Never viewed in a real Scratch/TurboWarp renderer.
* Text cursor, blink, VGA fonts: not implemented. Font is a placeholder rasterisation.
* No BIOS, interrupts, I/O ports, devices, disks, keyboard/mouse, protected mode, paging. No shifts, MUL/DIV, string ops, INT.
* 4112 SVG costumes make project load slow in the editor.
* Vanilla Scratch runs ~35k instr/s (frame budget 5000 => ~8 fps); TurboWarp recommended. Not measured in a browser.
* Memory is limited to 1088 KiB (list-per-bank, zero-filled in JSON).

## Decisions
See docs/architecture.md. Test-only dependencies: GNU as/ld, Unicorn, Capstone, scratch-vm, scratch-parser.

## Next tasks (v0.2)
1. Shifts/rotates, MUL/IMUL/DIV/IDIV (+#DE), string ops + REP, segment push/pop, far transfers.
2. INT/IRET and vectored exceptions through the IVT.
3. Visual verification of the renderer in TurboWarp; cursor.
4. Extend differential generator to the new opcodes.
