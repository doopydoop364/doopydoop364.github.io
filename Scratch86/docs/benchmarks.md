# Benchmarks (measured, not estimated)

Method: `tools/benchmarks/bench.cjs` loads `Scratch86-v0.1.sb3` headless, runs a real x86 workload through the Scratch-block CPU (`s86_test_run`, budget 20000/batch) and divides guest instructions by wall time. 3 consecutive runs in one VM; the first TurboWarp run includes compilation. Workloads: `tools/benchmarks/workloads.py` (`mixed` = 6 instr/iter ALU+memory, 20000 iters = 120003 instr; `alu` = 4 instr/iter, 80002 instr).
Environment: Linux 7.2 (CachyOS), Node v26.11.1, one machine, no other load during the runs.

| Date | VM | Workload | Instructions/s (run1 / run2 / run3) |
|---|---|---|---|
| 2026-10-21 | stock scratch-vm 5.0.300 (interpreter) | mixed | 33,180 / 34,468 / 34,572 |
| 2026-10-21 | stock scratch-vm 5.0.300 (interpreter) | alu | 36,367 / 37,494 / 38,399 |
| 2026-10-21 | TurboWarp scratch-vm 2.1.46 compiler (headless, git 371839d) | mixed | 1,521,366 / 1,815,647 / 2,836,959 |
| 2026-10-21 | TurboWarp scratch-vm 2.1.46 compiler (headless) | alu | 1,246,298 / 1,879,019 / 3,277,531 |

Not measured: browser Scratch with/without Turbo Mode, the packaged TurboWarp web build, display redraw cost, startup time, host RAM. Headless runs have no renderer, so pen-stamp cost is excluded. TurboWarp timings are in node, not in the browser.
