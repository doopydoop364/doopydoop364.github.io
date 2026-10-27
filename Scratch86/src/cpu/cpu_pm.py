"""Protected-mode segmentation and system instructions (generator code).

Hidden segment cache per register: SEG (selector), SEGBASE, SEGLIM (bytes-1, granularity applied),
SEGATTR (access byte + 256*flags), SEGPERM (derived: 1 usable, 2 readable, 4 writable, 8 expand-down).
Descriptor access byte: bit0 A, bit1 W/R, bit2 ED/C, bit3 code, bit4 S, bits5-6 DPL, bit7 P.

Feature status is tracked in docs/cpu.md; anything not yet implemented raises vector 255 (visible stop)
rather than behaving silently.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def build_pm(p, S, mem, P, new, set_size, fchk):
    ents = []
    for nm, args in (('lldt_do', ['sel']), ('ltr_do', ['sel']), ('verx_do', ['sel', 'write']), ('cr0_changed', []),
                     ('cr0_write', ['val']), ('lar_lsl_do', ['sel', 'is_lsl'])):
        new(nm, args)

    def two(name, lo, hi=None):
        pr = new(name)
        ents.append((lo, lo if hi is None else hi, name))
        return pr.body()

    def acc(a, bit):
        return floor(a / (2 ** bit)) % 2

    def fault_sel(b, vec, sel):
        b.call(P.cpu_fault_err, vec, sel - sel % 4)

    # ------------------------------------------------------------ descriptors --
    pr = new("desc_fetch", ["sel"])        # -> D_LO D_HI D_LIN, D_OK=0 (and #GP pending) when outside the table
    with pr.body() as (b, sel):
        b.set(S.D_OK, 0)
        with b.if_(eq(floor(sel / 4) % 2, 1)) as t:          # LDT
            t.set(S.TA, S.LDTR_BASE)
            t.set(S.TB, S.LDTR_LIM)
            with t.if_(eq(acc(S.LDTR_ATTR, 7), 0)) as u:     # no LDT loaded
                u.set(S.TB, -1)
        with b.else_() as t:
            t.set(S.TA, S.GDTR_BASE)
            t.set(S.TB, S.GDTR_LIM)
        with b.if_(floor(sel / 8) * 8 + 7 > S.TB) as t:
            t.call(P.cpu_fault_err, S.DESC_VEC, sel - sel % 4 + S.DESC_EXT)
            t.stop_script()
        b.set(S.D_LIN, (S.TA + floor(sel / 8) * 8) % 4294967296)
        b.call(mem.mem_rd, S.D_LIN)
        b.set(S.D_LO, S.MEMV)
        b.call(mem.mem_rd, (S.D_LIN + 4) % 4294967296)
        b.set(S.D_HI, S.MEMV)
        b.set(S.D_OK, 1)

    pr = new("desc_decode")                # D_LO/D_HI -> D_BASE D_LIM D_ATTR
    with pr.body() as (b,):
        b.set(S.D_BASE, floor(S.D_LO / 65536) + (S.D_HI % 256) * 65536 + floor(S.D_HI / 16777216) * 16777216)
        b.set(S.TA, S.D_LO % 65536 + (floor(S.D_HI / 65536) % 16) * 65536)     # raw limit
        b.set(S.D_ATTR, floor(S.D_HI / 256) % 256 + (floor(S.D_HI / 1048576) % 16) * 256)
        with b.if_(eq(floor(S.D_ATTR / 2048) % 2, 1)) as t:                   # granularity
            t.set(S.D_LIM, S.TA * 4096 + 4095)
        with b.else_() as t:
            t.set(S.D_LIM, S.TA)

    def commit(b, idx, sel):
        """Cache the decoded descriptor in segment register `idx` and derive SEGPERM."""
        b.replace(S.SEG, idx + 1, sel)
        b.replace(S.SEGBASE, idx + 1, S.D_BASE)
        b.replace(S.SEGLIM, idx + 1, S.D_LIM)
        b.replace(S.SEGATTR, idx + 1, S.D_ATTR)
        # code: usable, readable if R; data: usable, readable, writable if W, expand-down if ED
        with b.if_(eq(acc(S.D_ATTR, 3), 1)) as t:
            t.replace(S.SEGPERM, idx + 1, 1 + 2 * acc(S.D_ATTR, 1))
        with b.else_() as t:
            t.replace(S.SEGPERM, idx + 1, 3 + 4 * acc(S.D_ATTR, 1) + 8 * acc(S.D_ATTR, 2))

    def set_accessed(b):
        with b.if_(eq(S.D_ATTR % 2, 0)) as t:
            t.call(mem.mem_wb, (S.D_LIN + 5) % 4294967296, S.D_ATTR % 256 + 1)
            t.set(S.D_ATTR, S.D_ATTR + 1)

    with P.seg_load_pm.body() as (b, idx, sel):
        b.set(S.TD, sel % 4)                                 # RPL
        with b.if_(eq(idx, 2)) as t:                         # --- SS
            with t.if_(sel < 4) as u:
                u.call(P.cpu_fault_err, 13, 0)
                u.stop_script()
            t.call(P.desc_fetch, sel)
            fchk(t)
            t.call(P.desc_decode)
            t.set(S.X8, S.D_ATTR)
            with t.if_(OR(eq(acc(S.X8, 4), 0), eq(acc(S.X8, 3), 1), eq(acc(S.X8, 1), 0),
                          ne(S.TD, S.CPL), ne(floor(S.X8 / 32) % 4, S.CPL))) as u:
                fault_sel(u, 13, sel)
                u.stop_script()
            with t.if_(eq(acc(S.X8, 7), 0)) as u:
                fault_sel(u, 12, sel)
                u.stop_script()
            set_accessed(t)
            commit(t, 2, sel)
            t.set(S.STK32, acc(S.D_ATTR, 10))
        with b.else_() as t:                                 # --- ES DS FS GS
            with t.if_(S_lt(sel, 4)) as u:                   # null selector: loadable, unusable
                u.replace(S.SEG, idx + 1, sel)
                u.replace(S.SEGBASE, idx + 1, 0)
                u.replace(S.SEGLIM, idx + 1, 0)
                u.replace(S.SEGATTR, idx + 1, 0)
                u.replace(S.SEGPERM, idx + 1, 0)
                u.stop_script()
            t.call(P.desc_fetch, sel)
            fchk(t)
            t.call(P.desc_decode)
            t.set(S.X8, S.D_ATTR)
            with t.if_(eq(acc(S.X8, 4), 0)) as u:            # system descriptor
                fault_sel(u, 13, sel)
                u.stop_script()
            with t.if_(AND(eq(acc(S.X8, 3), 1), eq(acc(S.X8, 1), 0))) as u:   # execute-only code
                fault_sel(u, 13, sel)
                u.stop_script()
            # privilege: max(CPL, RPL) <= DPL, except conforming code
            t.set(S.X7, S.CPL)
            with t.if_(S.TD > S.CPL) as u:
                u.set(S.X7, S.TD)
            with t.if_(NOT(AND(eq(acc(S.X8, 3), 1), eq(acc(S.X8, 2), 1)))) as u:
                with u.if_(S.X7 > floor(S.X8 / 32) % 4) as w:
                    fault_sel(w, 13, sel)
                    w.stop_script()
            with t.if_(eq(acc(S.X8, 7), 0)) as u:
                fault_sel(u, 11, sel)
                u.stop_script()
            set_accessed(t)
            commit(t, idx, sel)

    # ------------------------------------------------------------- far transfers --
    # far_xfer(sel, off, is_call): far JMP/CALL.  Real mode: plain CS load.  Protected mode: direct
    # code-segment targets; call/task/TSS gates are v0.3 slice B (visible stop for now).
    pr = new("far_xfer", ["sel", "off", "is_call"])
    with pr.body() as (b, sel, off, is_call):
        with b.if_(eq(S.CR0 % 2, 0)) as t:
            with t.if_(eq(is_call, 1)) as u:
                u.call(P.push, S.OSZ, S.SEG[2])
                u.call(P.push, S.OSZ, S.EIP)
            t.replace(S.SEG, 2, sel)
            t.replace(S.SEGBASE, 2, sel * 16)
            t.set(S.EIP, off % S.CODE_MASK)
            t.stop_script()
        with b.if_(S_lt(sel, 4)) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        b.call(P.desc_fetch, sel)
        fchk(b)
        b.call(P.desc_decode)
        b.set(S.X8, S.D_ATTR)
        with b.if_(eq(acc(S.X8, 4), 0)) as t:                # gates / TSS
            t.call(P.cpu_fault, 256)
            t.stop_script()
        with b.if_(eq(acc(S.X8, 3), 0)) as t:                # data segment
            fault_sel(t, 13, sel)
            t.stop_script()
        b.set(S.X7, floor(S.X8 / 32) % 4)                    # DPL
        with b.if_(eq(acc(S.X8, 2), 1)) as t:                # conforming: DPL <= CPL
            with t.if_(S.X7 > S.CPL) as u:
                fault_sel(u, 13, sel)
                u.stop_script()
        with b.else_() as t:                                 # non-conforming: DPL == CPL and RPL <= CPL
            with t.if_(OR(ne(S.X7, S.CPL), sel % 4 > S.CPL)) as u:
                fault_sel(u, 13, sel)
                u.stop_script()
        with b.if_(eq(acc(S.X8, 7), 0)) as t:
            fault_sel(t, 11, sel)
            t.stop_script()
        with b.if_(off > S.D_LIM) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        with b.if_(eq(is_call, 1)) as t:
            t.call(P.push, S.OSZ, S.SEG[2])
            t.call(P.push, S.OSZ, S.EIP)
            fchk(t)
        set_accessed(b)
        commit(b, 1, sel - sel % 4 + S.CPL)                  # CS.RPL = CPL
        b.set(S.DEF_OSZ, 2 + 2 * acc(S.D_ATTR, 10))
        b.set(S.DEF_ASZ, 2 + 2 * acc(S.D_ATTR, 10))
        b.set(S.CODE_MASK, 65536 + 4294901760 * acc(S.D_ATTR, 10))
        b.set(S.EIP, off)

    # cs_ret_load(sel, off): CS/EIP load for RETF and IRET (no pushes).  Same-privilege returns only;
    # returns to an outer level need the SS:ESP pop (slice B) and stop visibly via vector 255.
    pr = new("cs_ret_load", ["sel", "off"])
    with pr.body() as (b, sel, off):
        with b.if_(eq(S.CR0 % 2, 0)) as t:
            t.replace(S.SEG, 2, sel)
            t.replace(S.SEGBASE, 2, sel * 16)
            t.set(S.EIP, off % S.CODE_MASK)
            t.stop_script()
        with b.if_(S_lt(sel, 4)) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        with b.if_(sel % 4 < S.CPL) as t:
            fault_sel(t, 13, sel)
            t.stop_script()
        with b.if_(sel % 4 > S.CPL) as t:
            t.call(P.cpu_fault, 256)
            t.stop_script()
        b.call(P.desc_fetch, sel)
        fchk(b)
        b.call(P.desc_decode)
        b.set(S.X8, S.D_ATTR)
        with b.if_(OR(eq(acc(S.X8, 4), 0), eq(acc(S.X8, 3), 0))) as t:
            fault_sel(t, 13, sel)
            t.stop_script()
        b.set(S.X7, floor(S.X8 / 32) % 4)
        with b.if_(eq(acc(S.X8, 2), 1)) as t:
            with t.if_(S.X7 > sel % 4) as u:
                fault_sel(u, 13, sel)
                u.stop_script()
        with b.else_() as t:
            with t.if_(ne(S.X7, sel % 4)) as u:
                fault_sel(u, 13, sel)
                u.stop_script()
        with b.if_(eq(acc(S.X8, 7), 0)) as t:
            fault_sel(t, 11, sel)
            t.stop_script()
        with b.if_(off > S.D_LIM) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        set_accessed(b)
        commit(b, 1, sel)
        b.set(S.DEF_OSZ, 2 + 2 * acc(S.D_ATTR, 10))
        b.set(S.DEF_ASZ, 2 + 2 * acc(S.D_ATTR, 10))
        b.set(S.CODE_MASK, 65536 + 4294901760 * acc(S.D_ATTR, 10))
        b.set(S.EIP, off)

    # ------------------------------------------------------------- 0F 00 group 6 --
    def need_pm(b):
        """Instructions undefined in real mode: #UD."""
        with b.if_(eq(S.CR0 % 2, 0)) as t:
            t.call(P.cpu_fault, 6)
            t.stop_script()

    def need_cpl0(b):
        with b.if_(AND(eq(S.CR0 % 2, 1), ne(S.CPL, 0))) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()

    with two("h_grp6", 0x100) as (b,):
        need_pm(b)
        b.call(P.modrm_decode)
        with b.if_(S.REG < 2) as t:                          # SLDT / STR
            with t.if_(eq(S.REG, 0)) as u:
                u.set(S.X1, S.LDTR_SEL)
            with t.else_() as u:
                u.set(S.X1, S.TR_SEL)
            with t.if_(eq(S.EA_REG, 1)) as u:
                u.call(P.rm_set, S.OSZ, S.X1)
            with t.else_() as u:
                u.call(P.rm_set, 2, S.X1)
            fchk(t)
        with b.else_() as t:
            with t.if_(S.REG < 4) as u:                      # LLDT / LTR
                need_cpl0(u)
                u.call(P.rm_get, 2)
                fchk(u)
                u.set(S.H3, S.RMV)
                with u.if_(eq(S.REG, 2)) as w:
                    w.call(P.lldt_do, S.H3)
                with u.else_() as w:
                    w.call(P.ltr_do, S.H3)
            with t.else_() as u:                             # VERR / VERW
                with u.if_(S.REG > 5) as w:
                    w.call(P.cpu_fault, 6)
                    w.stop_script()
                u.call(P.rm_get, 2)
                fchk(u)
                u.call(P.verx_do, S.RMV, S.REG - 4)

    pr = P.lldt_do
    with pr.body() as (b, sel):
        with b.if_(S_lt(sel, 4)) as t:
            t.set(S.LDTR_SEL, sel)
            t.set(S.LDTR_BASE, 0)
            t.set(S.LDTR_LIM, 0)
            t.set(S.LDTR_ATTR, 0)
            t.stop_script()
        with b.if_(eq(floor(sel / 4) % 2, 1)) as t:          # LDT selector must reference the GDT
            fault_sel(t, 13, sel)
            t.stop_script()
        b.call(P.desc_fetch, sel)
        fchk(b)
        b.call(P.desc_decode)
        with b.if_(OR(ne(S.D_ATTR % 32, 2))) as t:           # S=0 and type 2
            fault_sel(t, 13, sel)
            t.stop_script()
        with b.if_(eq(acc(S.D_ATTR, 7), 0)) as t:
            fault_sel(t, 11, sel)
            t.stop_script()
        b.set(S.LDTR_SEL, sel)
        b.set(S.LDTR_BASE, S.D_BASE)
        b.set(S.LDTR_LIM, S.D_LIM)
        b.set(S.LDTR_ATTR, S.D_ATTR)

    pr = P.ltr_do
    with pr.body() as (b, sel):
        with b.if_(S_lt(sel, 4)) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        with b.if_(eq(floor(sel / 4) % 2, 1)) as t:
            fault_sel(t, 13, sel)
            t.stop_script()
        b.call(P.desc_fetch, sel)
        fchk(b)
        b.call(P.desc_decode)
        with b.if_(NOT(OR(eq(S.D_ATTR % 32, 1), eq(S.D_ATTR % 32, 9)))) as t:   # available 286/386 TSS
            fault_sel(t, 13, sel)
            t.stop_script()
        with b.if_(eq(acc(S.D_ATTR, 7), 0)) as t:
            fault_sel(t, 11, sel)
            t.stop_script()
        b.call(mem.mem_wb, (S.D_LIN + 5) % 4294967296, S.D_ATTR % 256 + 2)    # mark busy
        b.set(S.TR_SEL, sel)
        b.set(S.TR_BASE, S.D_BASE)
        b.set(S.TR_LIM, S.D_LIM)
        b.set(S.TR_ATTR, S.D_ATTR + 2)

    pr = P.verx_do    # VERR (0) / VERW (1): ZF <- accessible
    with pr.body() as (b, sel, write):
        b.set(S.ZF, 0)
        with b.if_(S_lt(sel, 4)) as t:
            t.stop_script()
        b.set(S.TD, sel % 4)
        # descriptor fetch must not fault here: bound check by hand
        with b.if_(eq(floor(sel / 4) % 2, 1)) as t:
            t.set(S.TA, S.LDTR_BASE)
            t.set(S.TB, S.LDTR_LIM)
        with b.else_() as t:
            t.set(S.TA, S.GDTR_BASE)
            t.set(S.TB, S.GDTR_LIM)
        with b.if_(floor(sel / 8) * 8 + 7 > S.TB) as t:
            t.stop_script()
        b.set(S.D_LIN, (S.TA + floor(sel / 8) * 8) % 4294967296)
        b.call(mem.mem_rd, (S.D_LIN + 4) % 4294967296)
        b.set(S.X8, floor(S.MEMV / 256) % 256)                # access byte
        with b.if_(eq(acc(S.X8, 4), 0)) as t:
            t.stop_script()
        b.set(S.X7, S.CPL)
        with b.if_(S.TD > S.CPL) as t:
            t.set(S.X7, S.TD)
        b.set(S.X6, 1)                                        # privilege ok?
        with b.if_(NOT(AND(eq(acc(S.X8, 3), 1), eq(acc(S.X8, 2), 1)))) as t:
            with t.if_(S.X7 > floor(S.X8 / 32) % 4) as u:
                u.set(S.X6, 0)
        with b.if_(eq(S.X6, 1)) as t:
            with t.if_(eq(write, 0)) as u:                    # readable: data, or code with R
                with u.if_(OR(eq(acc(S.X8, 3), 0), eq(acc(S.X8, 1), 1))) as w:
                    w.set(S.ZF, 1)
            with t.else_() as u:                              # writable data
                with u.if_(AND(eq(acc(S.X8, 3), 0), eq(acc(S.X8, 1), 1))) as w:
                    w.set(S.ZF, 1)

    # ------------------------------------------------------------- 0F 01 group 7 --
    with two("h_grp7", 0x101) as (b,):
        b.call(P.modrm_decode)
        with b.if_(S.REG < 4) as t:                          # SGDT SIDT LGDT LIDT: memory operand only
            with t.if_(eq(S.EA_REG, 1)) as u:
                u.call(P.cpu_fault, 6)
                u.stop_script()
            with t.if_(S.REG > 1) as u:
                need_cpl0(u)
            with t.if_(S.REG < 2) as u:
                u.call(P.ea_chk, 6, 1)
            with t.else_() as u:
                u.call(P.ea_chk, 6, 0)
            fchk(t)
            with t.if_(S.REG < 2) as u:                       # store
                with u.if_(eq(S.REG, 0)) as w:
                    w.set(S.X1, S.GDTR_LIM)
                    w.set(S.X2, S.GDTR_BASE)
                with u.else_() as w:
                    w.set(S.X1, S.IDTR_LIM)
                    w.set(S.X2, S.IDTR_BASE)
                with u.if_(eq(S.OSZ, 2)) as w:
                    w.set(S.X2, S.X2 % 16777216)
                u.call(mem.mem_ww, S.EA_LIN, S.X1)
                u.call(mem.mem_wd, (S.EA_LIN + 2) % 4294967296, S.X2)
            with t.else_() as u:                              # load
                u.call(mem.mem_rw, S.EA_LIN)
                u.set(S.X1, S.MEMV)
                u.call(mem.mem_rd, (S.EA_LIN + 2) % 4294967296)
                u.set(S.X2, S.MEMV)
                with u.if_(eq(S.OSZ, 2)) as w:
                    w.set(S.X2, S.X2 % 16777216)
                with u.if_(eq(S.REG, 2)) as w:
                    w.set(S.GDTR_LIM, S.X1)
                    w.set(S.GDTR_BASE, S.X2)
                with u.else_() as w:
                    w.set(S.IDTR_LIM, S.X1)
                    w.set(S.IDTR_BASE, S.X2)
        with b.else_() as t:
            with t.if_(eq(S.REG, 4)) as u:                    # SMSW
                with u.if_(eq(S.EA_REG, 1)) as w:
                    w.call(P.rm_set, S.OSZ, S.CR0 % 65536)
                with u.else_() as w:
                    w.call(P.rm_set, 2, S.CR0 % 65536)
                fchk(u)
            with t.else_() as u:
                with u.if_(eq(S.REG, 6)) as w:                # LMSW (cannot clear PE)
                    need_cpl0(w)
                    w.call(P.rm_get, 2)
                    fchk(w)
                    w.set(S.X1, S.RMV % 16)
                    with w.if_(eq(S.CR0 % 2, 1)) as x:
                        x.set(S.X1, S.X1 - S.X1 % 2 + 1)
                    w.set(S.CR0, S.CR0 - S.CR0 % 16 + S.X1)
                    w.call(P.cr0_changed)
                with u.else_() as w:
                    with w.if_(AND(eq(S.REG, 7), ge(S.PROFILE, 4), eq(S.EA_REG, 0))) as x:
                        need_cpl0(x)                         # INVLPG (486+): no TLB until v0.4
                    with w.else_() as x:
                        x.call(P.cpu_fault, 6)

    with P.cr0_changed.body() as (b,):
        b.set(S.CPU_MODE, S.CR0 % 2)

    with two("h_clts", 0x106) as (b,):
        need_cpl0(b)
        b.set(S.CR0, S.CR0 - (floor(S.CR0 / 8) % 2) * 8)

    with two("h_mov_cr", 0x120, 0x123) as (b,):
        b.call(P.modrm_decode)
        need_cpl0(b)
        with b.if_(eq(S.OPCODE % 2, 1)) as t:                # 0F 21/23 MOV DRn: debug registers are not implemented
            t.call(P.cpu_fault, 256)
            t.stop_script()
        with b.if_(NOT(OR(eq(S.REG, 0), eq(S.REG, 2), eq(S.REG, 3), AND(eq(S.REG, 4), ge(S.PROFILE, 5))))) as t:
            t.call(P.cpu_fault, 6)
            t.stop_script()
        with b.if_(S.OPCODE < 0x122) as t:                   # 0F 20 MOV r32, CRn  (0F 22: MOV CRn, r32)
            t.dispatch(S.REG, [(0, 0, lambda u: u.set(S.X1, S.CR0)), (2, 2, lambda u: u.set(S.X1, S.CR2)),
                               (3, 3, lambda u: u.set(S.X1, S.CR3)), (4, 4, lambda u: u.set(S.X1, S.CR4))])
            t.call(P.reg_set, 4, S.RM, S.X1)
        with b.else_() as t:                                 # MOV CRn, r32
            t.call(P.reg_get, 4, S.RM)
            t.set(S.X1, S.RV)
            t.dispatch(S.REG, [
                (0, 0, lambda u: u.call(P.cr0_write, S.X1)),
                (2, 2, lambda u: u.set(S.CR2, S.X1)),
                (3, 3, lambda u: u.set(S.CR3, S.X1)),
                (4, 4, lambda u: u.set(S.CR4, S.X1))])

    pr = P.cr0_write
    with pr.body() as (b, val):
        # writable bits: 386 = PG ET TS EM MP PE; 486 adds CD NW AM WP NE
        with b.if_(S.PROFILE < 4) as t:
            t.set(S.X2, val % 16 + floor(val / 2147483648) * 2147483648)
        with b.else_() as t:
            t.set(S.X2, (val % 64) + (floor(val / 65536) % 2) * 65536 + (floor(val / 262144) % 2) * 262144 +
                  floor(val / 536870912) * 536870912)
        with b.if_(AND(ge(val, 2147483648), eq(val % 2, 0))) as t:     # PG without PE
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        b.set(S.CR0, S.X2 - (floor(S.X2 / 16) % 2) * 16 + 16)      # ET reads as 1
        b.call(P.cr0_changed)

    with two("h_lar_lsl", 0x102, 0x103) as (b,):
        need_pm(b)
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        b.call(P.rm_get, 2)
        fchk(b)
        b.call(P.lar_lsl_do, S.RMV, S.OPCODE - 0x102)

    pr = P.lar_lsl_do
    with pr.body() as (b, sel, is_lsl):
        b.set(S.ZF, 0)
        with b.if_(S_lt(sel, 4)) as t:
            t.stop_script()
        b.set(S.TD, sel % 4)
        with b.if_(eq(floor(sel / 4) % 2, 1)) as t:
            t.set(S.TA, S.LDTR_BASE)
            t.set(S.TB, S.LDTR_LIM)
        with b.else_() as t:
            t.set(S.TA, S.GDTR_BASE)
            t.set(S.TB, S.GDTR_LIM)
        with b.if_(floor(sel / 8) * 8 + 7 > S.TB) as t:
            t.stop_script()
        b.set(S.D_LIN, (S.TA + floor(sel / 8) * 8) % 4294967296)
        b.call(mem.mem_rd, S.D_LIN)
        b.set(S.D_LO, S.MEMV)
        b.call(mem.mem_rd, (S.D_LIN + 4) % 4294967296)
        b.set(S.D_HI, S.MEMV)
        b.call(P.desc_decode)
        b.set(S.X8, S.D_ATTR)
        # valid types: code/data (S=1) and system types 1 2 3 4 5 9 B C (LAR); LSL excludes gates
        b.set(S.X6, 1)
        with b.if_(eq(acc(S.X8, 4), 0)) as t:
            t.set(S.X6, 0)
            with t.if_(OR(eq(S.X8 % 16, 1), eq(S.X8 % 16, 2), eq(S.X8 % 16, 3), eq(S.X8 % 16, 9), eq(S.X8 % 16, 11))) as u:
                u.set(S.X6, 1)
            with t.if_(AND(eq(is_lsl, 0), OR(eq(S.X8 % 16, 4), eq(S.X8 % 16, 5), eq(S.X8 % 16, 12)))) as u:
                u.set(S.X6, 1)
        with b.if_(AND(eq(S.X6, 1), eq(acc(S.X8, 4), 1))) as t:      # privilege (conforming code exempt)
            t.set(S.X7, S.CPL)
            with t.if_(S.TD > S.CPL) as u:
                u.set(S.X7, S.TD)
            with t.if_(NOT(AND(eq(acc(S.X8, 3), 1), eq(acc(S.X8, 2), 1)))) as u:
                with u.if_(S.X7 > floor(S.X8 / 32) % 4) as w:
                    w.set(S.X6, 0)
        with b.if_(AND(eq(S.X6, 1), eq(acc(S.X8, 4), 0))) as t:
            with t.if_(max_gt(S.CPL, S.TD, floor(S.X8 / 32) % 4)) as u:
                u.set(S.X6, 0)
        with b.if_(eq(S.X6, 1)) as t:
            t.set(S.ZF, 1)
            with t.if_(eq(is_lsl, 1)) as u:
                u.call(P.reg_set, S.OSZ, S.REG, S.D_LIM % S.SZ_M[S.OSZ])
            with t.else_() as u:
                u.call(P.reg_set, S.OSZ, S.REG, (floor(S.D_HI / 256) % 256 * 256 + (floor(S.D_HI / 1048576) % 16) * 1048576) % S.SZ_M[S.OSZ])   # access rights, masked 00F0FF00

    with two("h_wbinvd", 0x108, 0x109) as (b,):               # INVD / WBINVD (486+): no caches -> nop
        with b.if_(S.PROFILE < 4) as t:
            t.call(P.cpu_fault, 6)
            t.stop_script()
        need_cpl0(b)

    with new("h_arpl").body() as (b,):                       # 63
        need_pm(b)
        b.call(P.modrm_decode)
        b.call(P.rm_get, 2)
        fchk(b)
        b.set(S.H3, S.RMV)
        b.call(P.reg_get, 2, S.REG)
        with b.if_(S.H3 % 4 < S.RV % 4) as t:
            t.set(S.ZF, 1)
            t.call(P.rm_set, 2, S.H3 - S.H3 % 4 + S.RV % 4)
            fchk(t)
        with b.else_() as t:
            t.set(S.ZF, 0)
    ents.append((0x63, 0x63, "h_arpl"))

    from cpu_pm_int import build_pm_int
    build_pm_int(p, S, mem, P, new, fchk, (commit, set_accessed))
    return ents


def S_lt(a, n):
    return a < n


def max_gt(a, b, c):
    """max(a, b) > c as a Scratch boolean."""
    return OR(a > c, b > c)
