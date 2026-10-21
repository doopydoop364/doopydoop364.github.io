"""Physical memory subsystem (Scratch blocks generated via sb3gen).

Physical RAM is a set of Scratch lists ("banks") of 64 KiB, one byte per item.
Lists are used because they are the only random-access storage Scratch has,
and scratch-vm caps *appended* lists at 200000 items, so a single flat 1 MiB
list is not portable; 64 KiB banks also match a real-mode segment.

Address path for every data access / fetch:

    linear --(A20 gate mask)--> PHYS --(bank = PHYS div 65536)--> RAM_bank[off]

Paging is not implemented yet (v0.6), so linear == physical apart from the A20
mask.  Addresses beyond the installed banks read as 0xFF and ignore writes.

Procedures (all "run without screen refresh"):

    mem_rb(lin)  -> MEMV            mem_wb(lin, val)
    mem_rw(lin)  -> MEMV            mem_ww(lin, val)
    mem_rd(lin)  -> MEMV            mem_wd(lin, val)

Values are little-endian; unaligned and bank-crossing accesses fall back to
byte-wise slow paths.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def _xlate(b, S, lin):
    """Emit: PHYS = A20-masked `lin`; BANK / BOFF split."""
    with b.if_(eq(S.A20, 0)) as t:
        t.set(S.PHYS, lin % 1048576)
    with b.else_() as t:
        t.set(S.PHYS, lin % 4294967296)
    b.set(S.BANK, floor(S.PHYS / 65536))
    b.set(S.BOFF, S.PHYS % 65536 + 1)


def build(p, S):
    nb = S.RAM_BANKS
    procs = type("Procs", (), {})()

    def bank_cases(emit):
        return [(k, k, (lambda bb, k=k: emit(bb, S.RAM[k]))) for k in range(nb)]

    # ---- reads -----------------------------------------------------------------
    # mem_fb is the instruction-fetch byte read: same path as mem_rb but it
    # never touches the data-read diagnostics (fetches != data reads).
    for n, name in ((1, "mem_fb"), (1, "mem_rb"), (2, "mem_rw"), (4, "mem_rd")):
        proc = p.procedure(name, ["lin"])
        setattr(procs, name, proc)
        with proc.body() as (b, lin):
            _xlate(b, S, lin)
            if n == 1:
                b.dispatch(S.BANK, bank_cases(lambda bb, L: bb.set(S.MEMV, L[S.BOFF])),
                           default=lambda bb: bb.set(S.MEMV, 255))
            else:
                last = 65536 - (n - 1)     # highest BOFF that stays inside the bank
                rb = procs.mem_rb

                def fast(bb, L, n=n):
                    expr = L[S.BOFF]
                    for i in range(1, n):
                        expr = expr + L[S.BOFF + i] * (256 ** i)
                    bb.set(S.MEMV, expr)

                with b.if_(le(S.BOFF, last)) as t:
                    t.dispatch(S.BANK, bank_cases(fast),
                               default=lambda bb: bb.set(S.MEMV, 256 ** n - 1))
                with b.else_() as t:
                    # bank-crossing: assemble from bytes
                    t.call(rb, lin)
                    t.set(S.MEMT, S.MEMV)
                    for i in range(1, n):
                        t.call(rb, (lin + i) % 4294967296)
                        t.set(S.MEMT, S.MEMT + S.MEMV * (256 ** i))
                    t.set(S.MEMV, S.MEMT)
            if name != "mem_fb":
                with b.if_(S.DIAG_MEM) as t:
                    t.set(S.LAST_RD_LIN, lin)
                    t.set(S.LAST_RD_PHYS, lin % (1048576 + S.A20 * 4293918720))
                    t.set(S.LAST_MEM_VAL, S.MEMV)
                    t.set(S.LAST_MEM_W, n)
                    t.set(S.LAST_MEM_RW, 1)

    # ---- writes ----------------------------------------------------------------
    for n, name in ((1, "mem_wb"), (2, "mem_ww"), (4, "mem_wd")):
        proc = p.procedure(name, ["lin", "val"])
        setattr(procs, name, proc)
        with proc.body() as (b, lin, val):
            _xlate(b, S, lin)
            if n == 1:
                b.dispatch(S.BANK, bank_cases(lambda bb, L: bb.replace(L, S.BOFF, val)))
            else:
                last = 65536 - (n - 1)
                wb = procs.mem_wb

                def fast(bb, L, n=n, val=val):
                    for i in range(n):
                        piece = val if i == 0 else floor(val / (256 ** i))
                        bb.replace(L, S.BOFF + i if i else S.BOFF, piece % 256)

                with b.if_(le(S.BOFF, last)) as t:
                    t.dispatch(S.BANK, bank_cases(fast))
                with b.else_() as t:
                    for i in range(n):
                        piece = val if i == 0 else floor(val / (256 ** i))
                        t.call(wb, (lin + i) % 4294967296, piece % 256)
            with b.if_(S.DIAG_MEM) as t:
                t.set(S.LAST_WR_LIN, lin)
                t.set(S.LAST_WR_PHYS, lin % (1048576 + S.A20 * 4293918720))
                t.set(S.LAST_MEM_VAL, val)
                t.set(S.LAST_MEM_W, n)
                t.set(S.LAST_MEM_RW, 2)
    return procs
