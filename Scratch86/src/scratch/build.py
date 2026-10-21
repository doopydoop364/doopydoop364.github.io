"""Build Scratch86 .sb3 from the generator modules.

    python3 src/scratch/build.py [--out builds/Scratch86-v0.1.sb3]
"""
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for sub in ("tools/sb3_generator", "src/scratch", "src/memory", "src/cpu", "tools/assembler"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from sb3gen import Project, BLANK_SVG  # noqa: E402
import state  # noqa: E402
import memory  # noqa: E402
import core  # noqa: E402
import control  # noqa: E402


def build_project(boot_image=None, ram_banks=state.RAM_BANKS):
    p = Project()
    p.stage.costume("backdrop1", BLANK_SVG, 240, 180)
    S = state.declare(p, ram_banks)
    mem = memory.build(p, S)
    P = core.build(p, S, mem)
    control.build(p, S, mem, P)

    if boot_image is not None:
        S.BOOT_IMAGE_ITEMS = list(boot_image)
        p.lists["BOOT_IMAGE (loaded at 0000:7C00)"] = (S.BOOT_IMAGE.id, list(boot_image))

    # Test entry points (also useful as debugger hooks): fired by broadcast.
    with p.on_broadcast("s86_test_run") as b:
        b.set(S.TEST_DONE, 0)
        b.call(P.machine_boot_test)
        with b.while_not(OR_(S, b)) as w:
            w.call(P.cpu_run_batch, S.BUDGET)
        b.set(S.TEST_DONE, 1)
    with p.on_broadcast("s86_test_boot") as b:      # reset into the test configuration only
        b.call(P.machine_boot_test)
    with p.on_broadcast("s86_test_exec") as b:      # run from the current CPU state
        b.set(S.TEST_DONE, 0)
        with b.while_not(OR_(S, b)) as w:
            w.call(P.cpu_run_batch, S.BUDGET)
        b.set(S.TEST_DONE, 1)
    return p, S, P


def OR_(S, b):
    from sb3gen import OR, ne, ge
    return OR(ne(S.CPU_STATE, 1), ge(S.INSN_COUNT, S.TEST_LIMIT))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "builds", "Scratch86-v0.1.sb3"))
    a = ap.parse_args()
    proj, S, P = build_project()
    proj.save(a.out)
    nblocks = sum(len(t.blocks) for t in proj.targets)
    print("wrote", a.out, "blocks:", nblocks)
