"""Memory subsystem tests, run inside scratch-vm against the generated blocks."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import run_cases  # noqa: E402

RB, RW, RD, WB, WW, WD = 1, 2, 3, 4, 5, 6


def ops(*seq, vars=None, peek=()):
    c = {"name": "mem", "memops": [{"op": o, "addr": a, "val": v} for o, a, v in seq],
         "peek": list(peek), "vars": vars or {}}
    return run_cases([c])[0]


def test_byte_roundtrip_and_little_endian():
    r = ops((WD, 0x1000, 0x11223344), (RB, 0x1000, 0), (RB, 0x1001, 0), (RB, 0x1002, 0), (RB, 0x1003, 0),
            (RW, 0x1000, 0), (RW, 0x1002, 0), (RD, 0x1000, 0))
    assert r["memres"][1:] == [0x44, 0x33, 0x22, 0x11, 0x3344, 0x1122, 0x11223344]


def test_unaligned_word_and_dword():
    r = ops((WD, 0x2001, 0xAABBCCDD), (RD, 0x2001, 0), (RW, 0x2002, 0), (RB, 0x2000, 0), (RB, 0x2005, 0))
    assert r["memres"][1:] == [0xAABBCCDD, 0xBBCC, 0, 0]   # bytes DD CC BB AA at 2001..2004


def test_bank_crossing_access():
    # 64 KiB bank boundary at 0x10000
    r = ops((WD, 0xFFFE, 0x04030201), (RB, 0xFFFE, 0), (RB, 0xFFFF, 0), (RB, 0x10000, 0), (RB, 0x10001, 0),
            (RD, 0xFFFE, 0), (RW, 0xFFFF, 0), (WW, 0x1FFFF, 0xBEEF), (RW, 0x1FFFF, 0))
    assert r["memres"][1:6] == [1, 2, 3, 4, 0x04030201]
    assert r["memres"][6] == 0x0302
    assert r["memres"][8] == 0xBEEF


def test_value_edges():
    r = ops((WB, 0x30, 0xFF), (RB, 0x30, 0), (WW, 0x40, 0xFFFF), (RW, 0x40, 0), (WD, 0x50, 0xFFFFFFFF), (RD, 0x50, 0),
            (WD, 0x60, 0), (RD, 0x60, 0), (WD, 0x70, 0x80000000), (RD, 0x70, 0))
    assert r["memres"][1::2] == [0xFF, 0xFFFF, 0xFFFFFFFF, 0, 0x80000000]


def test_a20_wrap_and_hma():
    # A20 off: 0x100000 aliases 0x000000.  A20 on: 0x100000 is distinct (HMA).
    r = ops((WB, 0x0, 0x5A), (WB, 0x100000, 0xA5), (RB, 0x0, 0), (RB, 0x100000, 0), vars={"A20 gate enabled": 0})
    assert r["memres"][2:] == [0xA5, 0xA5]       # second write landed on address 0
    r = ops((WB, 0x0, 0x5A), (WB, 0x100000, 0xA5), (RB, 0x0, 0), (RB, 0x100000, 0), vars={"A20 gate enabled": 1})
    assert r["memres"][2:] == [0x5A, 0xA5]


def test_unmapped_memory():
    # beyond 1 MiB + 64 KiB: reads return all ones, writes are ignored
    r = ops((RB, 0x200000, 0), (RW, 0x200000, 0), (RD, 0x200000, 0), (WD, 0x200000, 0x1234), (RD, 0x200000, 0),
            vars={"A20 gate enabled": 1})
    assert [r["memres"][i] for i in (0, 1, 2, 4)] == [0xFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF]


def test_top_of_physical_ram():
    r = ops((WB, 0x10FFFF, 0x77), (RB, 0x10FFFF, 0), (WW, 0x10FFFF, 0x1234), (RB, 0x10FFFF, 0),
            vars={"A20 gate enabled": 1})
    assert r["memres"][1] == 0x77 and r["memres"][3] == 0x34
