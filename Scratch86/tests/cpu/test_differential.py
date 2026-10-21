"""Differential test: random programs run on Scratch86 (in scratch-vm) and on Unicorn.

Unicorn (QEMU's CPU core) is used only as an oracle for expected register,
flag, memory and EIP results.  Set S86_DIFF_N to run more programs
(e.g. S86_DIFF_N=3000), S86_DIFF_START to shift the seed range.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from difftest import make_case, compare  # noqa: E402
from harness import run_cases  # noqa: E402

N = int(os.environ.get("S86_DIFF_N", "150"))
START = int(os.environ.get("S86_DIFF_START", "0"))


def _run(allow32):
    cases = [make_case(s, allow32) for s in range(START, START + N)]
    results = run_cases(cases)
    bad, compared = [], 0
    for c, r in zip(cases, results):
        d = compare(c, r)
        if d is None:
            continue                    # reference faulted: not comparable
        compared += 1
        if d:
            bad.append((c["name"], c["src"], d))
    return bad, compared


def test_random_programs_16bit_only():
    bad, compared = _run(False)
    assert compared > N * 0.8
    assert not bad, bad[:3]


def test_random_programs_with_32bit_operands_and_addressing():
    bad, compared = _run(True)
    assert compared > N * 0.6
    assert not bad, bad[:3]
