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
    _paging(p, S, procs)

    def bank_cases(emit):
        return [(k, k, (lambda bb, k=k: emit(bb, S.RAM[k]))) for k in range(nb)]

    def translate(b, lin, wr):
        """Linear -> physical (paging when CR0.PG), then A20 mask and bank split.  A page fault returns
        from the calling memory procedure with the fault pending (reads yield 0)."""
        with b.if_(eq(S.PG, 1)) as t:
            t.call(procs.page_xlate, lin, wr)
            with t.if_(S.FAULT_PEND > -1) as u:
                u.set(S.MEMV, 0)
                u.stop_script()
        with b.else_() as t:
            t.set(S.PLIN, lin % 4294967296)
        _xlate(b, S, S.PLIN)

    # ---- reads -----------------------------------------------------------------
    # mem_fb is the instruction-fetch byte read: same path as mem_rb but it
    # never touches the data-read diagnostics (fetches != data reads).
    for n, name in ((1, "mem_fb"), (1, "mem_rb"), (2, "mem_rw"), (4, "mem_rd")):
        proc = p.procedure(name, ["lin"])
        setattr(procs, name, proc)
        with proc.body() as (b, lin):
            translate(b, lin, 0)
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

                with b.if_(AND(le(S.BOFF, last), OR(eq(S.PG, 0), le(lin % 4096 + n, 4096)))) as t:
                    t.dispatch(S.BANK, bank_cases(fast),
                               default=lambda bb: bb.set(S.MEMV, 256 ** n - 1))
                with b.else_() as t:
                    # bank- or page-crossing: assemble from bytes (each byte translates on its own)
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
            translate(b, lin, 1)
            if n == 1:
                b.dispatch(S.BANK, bank_cases(lambda bb, L: bb.replace(L, S.BOFF, val)))
            else:
                last = 65536 - (n - 1)
                wb = procs.mem_wb

                def fast(bb, L, n=n, val=val):
                    for i in range(n):
                        piece = val if i == 0 else floor(val / (256 ** i))
                        bb.replace(L, S.BOFF + i if i else S.BOFF, piece % 256)

                with b.if_(AND(le(S.BOFF, last), OR(eq(S.PG, 0), le(lin % 4096 + n, 4096)))) as t:
                    t.dispatch(S.BANK, bank_cases(fast))
                with b.else_() as t:
                    with t.if_(eq(S.PG, 1)) as u:            # probe the last byte's page before writing anything
                        u.call(procs.page_xlate, (lin + n - 1 - (lin + n - 1) % 4096) % 4294967296, 1)   # CR2 = first byte on the faulting page
                        with u.if_(S.FAULT_PEND > -1) as w:
                            w.stop_script()
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


def _paging(p, S, procs):
    """386-style two-level paging: direct-mapped TLB, page walk with accessed/dirty updates, #PF.

    page_xlate(lin, wr) -> PLIN (physical address before the A20 mask) or a pending #PF (CR2 set).
    Implicit supervisor accesses (descriptor tables, TSS, IDT) set SUPACC; user = CPL 3 and not SUPACC.
    """
    nb = S.RAM_BANKS

    def bank_cases(emit):
        return [(k, k, (lambda bb, k=k: emit(bb, S.RAM[k]))) for k in range(nb)]

    def phys(b, addr):
        with b.if_(eq(S.A20, 0)) as t:
            t.set(S.PHYS, addr % 1048576)
        with b.else_() as t:
            t.set(S.PHYS, addr % 4294967296)
        b.set(S.BANK, floor(S.PHYS / 65536))
        b.set(S.BOFF, S.PHYS % 65536 + 1)

    pr = p.procedure("mem_prd", ["addr"])            # raw physical dword read (aligned): page-table entries
    procs.mem_prd = pr
    with pr.body() as (b, addr):
        phys(b, addr)
        b.dispatch(S.BANK, bank_cases(lambda bb, L: bb.set(
            S.MEMV, L[S.BOFF] + L[S.BOFF + 1] * 256 + L[S.BOFF + 2] * 65536 + L[S.BOFF + 3] * 16777216)),
            default=lambda bb: bb.set(S.MEMV, 4294967295))

    pr = p.procedure("mem_pwd", ["addr", "val"])     # raw physical dword write (aligned)
    procs.mem_pwd = pr
    with pr.body() as (b, addr, val):
        phys(b, addr)

        def put(bb, L):
            for i in range(4):
                piece = val if i == 0 else floor(val / (256 ** i))
                bb.replace(L, S.BOFF + i if i else S.BOFF, piece % 256)
        b.dispatch(S.BANK, bank_cases(put))

    pr = p.procedure("tlb_flush")
    procs.tlb_flush = pr
    with pr.body() as (b,):
        b.set(S.PX1, 1)
        with b.repeat(64) as w:
            w.replace(S.TLB_TAG, S.PX1, -1)
            w.change(S.PX1, 1)

    pr = p.procedure("tlb_inval", ["lin"])
    procs.tlb_inval = pr
    with pr.body() as (b, lin):
        b.set(S.PX1, floor(lin / 4096))
        with b.if_(eq(S.TLB_TAG[S.PX1 % 64 + 1], S.PX1)) as t:
            t.replace(S.TLB_TAG, S.PX1 % 64 + 1, -1)

    pr = p.procedure("pf_raise", ["lin", "present", "wr"])
    procs.pf_raise = pr
    with pr.body() as (b, lin, present, wr):
        with b.if_(S.FAULT_PEND < 0) as t:
            t.set(S.CR2, lin)
            t.set(S.FAULT_PEND, 14)
            t.set(S.FAULT_ERR, present + wr * 2 + S.PX4 * 4)
            t.set(S.FAULT_TRAP, 0)
            t.set(S.FAULT_SOFT, 0)

    pr = p.procedure("page_xlate", ["lin", "wr"])
    procs.page_xlate = pr
    with pr.body() as (b, lin, wr):
        b.set(S.PX1, floor(lin / 4096))                         # virtual page number
        b.set(S.PX2, S.PX1 % 64)                                # TLB index
        b.set(S.PX3, lin % 4096)
        b.set(S.PX4, 0)                                         # user-mode access?
        with b.if_(AND(eq(S.CPL, 3), eq(S.SUPACC, 0))) as t:
            t.set(S.PX4, 1)
        b.set(S.PX9, floor(S.CR0 / 65536) % 2)                  # CR0.WP
        with b.if_(eq(S.TLB_TAG[S.PX2 + 1], S.PX1)) as t:       # --- TLB hit
            t.set(S.PX5, S.TLB_FLAGS[S.PX2 + 1])
            t.set(S.PX6, 0)
            with t.if_(AND(eq(S.PX4, 1), eq(floor(S.PX5 / 2) % 2, 0))) as u:
                u.set(S.PX6, 1)
            with t.if_(AND(eq(wr, 1), eq(S.PX5 % 2, 0), OR(eq(S.PX4, 1), eq(S.PX9, 1)))) as u:
                u.set(S.PX6, 1)
            with t.if_(eq(S.PX6, 1)) as u:
                u.call(procs.pf_raise, lin, 1, wr)
                u.stop_script()
            with t.if_(OR(eq(wr, 0), eq(floor(S.PX5 / 4) % 2, 1))) as u:    # no dirty-bit update needed
                u.set(S.PLIN, S.TLB_PHYS[S.PX2 + 1] + S.PX3)
                u.stop_script()
        # --- page walk
        b.set(S.PX8, floor(S.CR3 / 4096) * 4096 + floor(S.PX1 / 1024) * 4)       # PDE address
        b.call(procs.mem_prd, S.PX8)
        b.set(S.PX5, S.MEMV)                                    # PDE
        with b.if_(eq(S.PX5 % 2, 0)) as t:
            t.call(procs.pf_raise, lin, 0, wr)
            t.stop_script()
        b.set(S.PX10, floor(S.PX5 / 4096) * 4096 + (S.PX1 % 1024) * 4)           # PTE address
        b.call(procs.mem_prd, S.PX10)
        b.set(S.PX7, S.MEMV)                                    # PTE
        with b.if_(eq(S.PX7 % 2, 0)) as t:
            t.call(procs.pf_raise, lin, 0, wr)
            t.stop_script()
        b.set(S.PX6, (floor(S.PX5 / 2) % 2) * (floor(S.PX7 / 2) % 2))            # effective writable
        b.set(S.PX11, (floor(S.PX5 / 4) % 2) * (floor(S.PX7 / 4) % 2))           # effective user
        with b.if_(AND(eq(S.PX4, 1), eq(S.PX11, 0))) as t:
            t.call(procs.pf_raise, lin, 1, wr)
            t.stop_script()
        with b.if_(AND(eq(wr, 1), eq(S.PX6, 0), OR(eq(S.PX4, 1), eq(S.PX9, 1)))) as t:
            t.call(procs.pf_raise, lin, 1, wr)
            t.stop_script()
        with b.if_(eq(floor(S.PX5 / 32) % 2, 0)) as t:          # PDE accessed
            t.call(procs.mem_pwd, S.PX8, S.PX5 + 32)
        b.set(S.PX2, S.PX1 % 64)                                # (mem procs clobber nothing of ours; keep explicit)
        b.set(S.PX3, lin % 4096)
        b.set(S.PX9, S.PX7)                                     # PTE as updated
        with b.if_(eq(floor(S.PX9 / 32) % 2, 0)) as t:
            t.set(S.PX9, S.PX9 + 32)
        with b.if_(AND(eq(wr, 1), eq(floor(S.PX9 / 64) % 2, 0))) as t:
            t.set(S.PX9, S.PX9 + 64)
        with b.if_(ne(S.PX9, S.PX7)) as t:
            t.call(procs.mem_pwd, S.PX10, S.PX9)
        b.replace(S.TLB_TAG, S.PX2 + 1, S.PX1)
        b.replace(S.TLB_PHYS, S.PX2 + 1, floor(S.PX9 / 4096) * 4096)
        b.replace(S.TLB_FLAGS, S.PX2 + 1, S.PX6 + 2 * S.PX11 + 4 * (floor(S.PX9 / 64) % 2))
        b.set(S.PLIN, floor(S.PX9 / 4096) * 4096 + S.PX3)
