# Roadmap
Versions are earned by meeting their acceptance criteria, not by calendar.

| Version | Scope | Acceptance |
|---|---|---|
| v0.1 | CPU foundation (done) | demo boots, 31 tests |
| v0.2 | Comprehensive real-mode CPU: arithmetic, shifts, MUL/DIV, strings+REP, INT/IRET, far transfers, BCD, 0F integer ops, faults | extensively verified (in progress - see DEVELOPMENT_PROGRESS.md) |
| v0.3 | 32-bit architecture: protected mode, descriptors, privilege, gates, TSS, exceptions, CPU profiles | isolated protected-mode programs incl. privilege/exception tests |
| v0.4 | Memory system: 1-128 MiB, paging, TLB, MMIO/ROM regions | paging tests at several RAM sizes |
| v0.5 | PC hardware: PIC, PIT, CMOS/RTC, 8042+PS/2, FDC, IDE, VGA, serial, DMA | register-level + integration tests |
| v0.6 | BIOS/firmware, boot path | boot-sector programs through the normal BIOS path |
| v0.7 | 486/Pentium features: CPUID, RDTSC, CMPXCHG8B, x87, V86 | subset tests, gaps documented |
| v0.8 | Performance engineering | reproducible before/after measurements |
| v0.9 | OS compatibility matrix | honest per-guest results |
| v1.0 | Stable general-purpose 32-bit PC emulator | all above, documented |
