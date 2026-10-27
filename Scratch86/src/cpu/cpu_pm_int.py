"""Protected-mode interrupt/exception delivery, privilege transitions and far returns (generator code).

    pm_deliver_one(vec, err, soft)   one delivery attempt through the IDT (interrupt/trap gates, same-level
                                     and inner-level via the TSS stack); may leave a nested fault pending
    pm_exc                           delivery loop with the #DF / triple-fault rules (called by control.exc_deliver)
    ret_far_pm(is_iret, imm)         RETF / IRET to the same or an outer privilege level
    io_check(port, size)             IOPL / TSS I-O permission bitmap

Task gates, task switching (NT / TSS jumps), call gates and virtual-8086 mode are not implemented yet; the
paths that would need them stop visibly with vector 256.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def build_pm_int(p, S, mem, P, new, fchk, helpers):
    commit, set_accessed = helpers

    def acc(a, bit):
        return floor(a / (2 ** bit)) % 2

    def ferr(b, vec, code):
        b.call(P.cpu_fault_err, vec, code)

    def ret_if_fault(b):
        with b.if_(S.FAULT_PEND > -1) as t:
            t.stop_script()

    # ------------------------------------------------------------- one delivery --
    with new("pm_deliver_one", ["vec", "err", "soft"]).body() as (b, vec, err, soft):
        b.set(S.SUPACC, 1)                                          # IDT/GDT/TSS/stack accesses of a delivery are supervisor accesses
        b.set(S.E_VEC, vec)
        b.set(S.E_SOFT, soft)
        b.set(S.E_EXT, 1 - soft)
        b.set(S.E_ERR, err)
        b.set(S.E_HAS, 0)
        with b.if_(AND(eq(soft, 0), OR(eq(vec, 8), AND(vec > 9, vec < 15), eq(vec, 17)))) as t:
            t.set(S.E_HAS, 1)
        b.set(S.E_IDT, vec * 8 + 2 + S.E_EXT)                       # error code if the IDT entry is bad
        with b.if_(vec * 8 + 7 > S.IDTR_LIM) as t:
            ferr(t, 13, S.E_IDT)
            t.stop_script()
        b.set(S.D_LIN, (S.IDTR_BASE + vec * 8) % 4294967296)
        b.call(mem.mem_rd, S.D_LIN)
        b.set(S.D_LO, S.MEMV)
        b.call(mem.mem_rd, (S.D_LIN + 4) % 4294967296)
        b.set(S.D_HI, S.MEMV)
        b.set(S.E_TYPE, floor(S.D_HI / 256) % 32)
        b.set(S.TD, floor(S.D_HI / 8192) % 4)                       # gate DPL
        with b.if_(NOT(OR(eq(S.E_TYPE, 5), eq(S.E_TYPE, 6), eq(S.E_TYPE, 7), eq(S.E_TYPE, 14), eq(S.E_TYPE, 15)))) as t:
            ferr(t, 13, S.E_IDT)
            t.stop_script()
        with b.if_(AND(eq(soft, 1), S.CPL > S.TD)) as t:
            ferr(t, 13, S.E_IDT)
            t.stop_script()
        with b.if_(eq(acc(S.D_HI, 15), 0)) as t:
            ferr(t, 11, S.E_IDT)
            t.stop_script()
        with b.if_(eq(S.E_TYPE, 5)) as t:                           # task gate
            t.call(P.cpu_fault, 256)
            t.stop_script()
        b.set(S.E_SEL, floor(S.D_LO / 65536))
        b.set(S.E_OFF, S.D_LO % 65536)
        b.set(S.E_SZ, 2)
        with b.if_(S.E_TYPE > 13) as t:
            t.set(S.E_OFF, S.E_OFF + floor(S.D_HI / 65536) * 65536)
            t.set(S.E_SZ, 4)
        b.set(S.E_SERR, S.E_SEL - S.E_SEL % 4 + S.E_EXT)            # error code for selector faults
        with b.if_(S.E_SEL < 4) as t:
            ferr(t, 13, S.E_EXT)
            t.stop_script()
        b.set(S.DESC_EXT, S.E_EXT)
        b.call(P.desc_fetch, S.E_SEL)
        b.set(S.DESC_EXT, 0)
        ret_if_fault(b)
        b.call(P.desc_decode)
        b.set(S.E_CATTR, S.D_ATTR)
        b.set(S.E_CBASE, S.D_BASE)
        b.set(S.E_CLIM, S.D_LIM)
        b.set(S.E_CLIN, S.D_LIN)
        with b.if_(OR(eq(acc(S.E_CATTR, 4), 0), eq(acc(S.E_CATTR, 3), 0))) as t:
            ferr(t, 13, S.E_SERR)
            t.stop_script()
        b.set(S.X7, floor(S.E_CATTR / 32) % 4)                      # code DPL
        with b.if_(S.X7 > S.CPL) as t:
            ferr(t, 13, S.E_SERR)
            t.stop_script()
        with b.if_(eq(acc(S.E_CATTR, 7), 0)) as t:
            ferr(t, 11, S.E_SERR)
            t.stop_script()
        b.set(S.E_NCPL, S.CPL)
        with b.if_(eq(acc(S.E_CATTR, 2), 0)) as t:                  # non-conforming: runs at its DPL
            t.set(S.E_NCPL, S.X7)
        with b.if_(S.E_OFF > S.E_CLIM) as t:
            ferr(t, 13, S.E_EXT)
            t.stop_script()
        b.call(P.flags_pack)
        b.set(S.E_EFL, S.EFL)
        with b.if_(S.E_NCPL < S.CPL) as t:                          # --- inner privilege level: switch stacks via the TSS
            t.set(S.E_OSS, S.SEG[3])
            t.set(S.E_OESP, S.GPR[5])
            t.set(S.E_TERR, S.TR_SEL - S.TR_SEL % 4 + S.E_EXT)
            with t.if_(floor(S.TR_ATTR % 16 / 8) > 0) as u:         # 32-bit TSS
                u.set(S.TA, 4 + 8 * S.E_NCPL)
                with u.if_(S.TA + 7 > S.TR_LIM) as w:
                    ferr(w, 10, S.E_TERR)
                    w.stop_script()
                u.call(mem.mem_rd, (S.TR_BASE + S.TA) % 4294967296)
                u.set(S.E_NESP, S.MEMV)
                u.call(mem.mem_rw, (S.TR_BASE + S.TA + 4) % 4294967296)
                u.set(S.E_NSS, S.MEMV)
            with t.else_() as u:                                    # 16-bit TSS
                u.set(S.TA, 2 + 4 * S.E_NCPL)
                with u.if_(S.TA + 3 > S.TR_LIM) as w:
                    ferr(w, 10, S.E_TERR)
                    w.stop_script()
                u.call(mem.mem_rw, (S.TR_BASE + S.TA) % 4294967296)
                u.set(S.E_NESP, S.MEMV)
                u.call(mem.mem_rw, (S.TR_BASE + S.TA + 2) % 4294967296)
                u.set(S.E_NSS, S.MEMV)
            with t.if_(S.E_NSS < 4) as u:
                ferr(u, 10, S.E_EXT)
                u.stop_script()
            t.set(S.DESC_VEC, 10)
            t.set(S.DESC_EXT, S.E_EXT)
            t.call(P.desc_fetch, S.E_NSS)
            t.set(S.DESC_VEC, 13)
            t.set(S.DESC_EXT, 0)
            ret_if_fault(t)
            t.call(P.desc_decode)
            t.set(S.E_TERR, S.E_NSS - S.E_NSS % 4 + S.E_EXT)
            with t.if_(OR(eq(acc(S.D_ATTR, 4), 0), eq(acc(S.D_ATTR, 3), 1), eq(acc(S.D_ATTR, 1), 0),
                          ne(S.E_NSS % 4, S.E_NCPL), ne(floor(S.D_ATTR / 32) % 4, S.E_NCPL))) as u:
                ferr(u, 10, S.E_TERR)
                u.stop_script()
            with t.if_(eq(acc(S.D_ATTR, 7), 0)) as u:
                ferr(u, 12, S.E_TERR)
                u.stop_script()
            set_accessed(t)
            commit(t, 2, S.E_NSS)
            t.set(S.STK32, acc(S.D_ATTR, 10))
            t.replace(S.GPR, 5, S.E_NESP)
            t.call(P.push, S.E_SZ, S.E_OSS)
            t.call(P.push, S.E_SZ, S.E_OESP)
        b.call(P.push, S.E_SZ, S.E_EFL)
        b.call(P.push, S.E_SZ, S.SEG[2])
        b.call(P.push, S.E_SZ, S.EIP)
        with b.if_(eq(S.E_HAS, 1)) as t:
            t.call(P.push, S.E_SZ, S.E_ERR)
        ret_if_fault(b)
        # commit the new code segment
        b.set(S.D_BASE, S.E_CBASE)
        b.set(S.D_LIM, S.E_CLIM)
        b.set(S.D_ATTR, S.E_CATTR)
        b.set(S.D_LIN, S.E_CLIN)
        set_accessed(b)
        commit(b, 1, S.E_SEL - S.E_SEL % 4 + S.E_NCPL)
        b.set(S.CPL, S.E_NCPL)
        b.set(S.DEF_OSZ, 2 + 2 * acc(S.D_ATTR, 10))
        b.set(S.DEF_ASZ, 2 + 2 * acc(S.D_ATTR, 10))
        b.set(S.CODE_MASK, 65536 + 4294901760 * acc(S.D_ATTR, 10))
        b.set(S.EIP, S.E_OFF)
        with b.if_(eq(S.E_TYPE % 2, 0)) as t:                        # interrupt gates clear IF; trap gates keep it
            t.set(S.IF, 0)
        b.set(S.TF, 0)
        b.set(S.EFL_OTHER, S.EFL_OTHER - (acc(S.EFL_OTHER, 14)) * 16384 - acc(S.EFL_OTHER, 16) * 65536 -
              acc(S.EFL_OTHER, 17) * 131072)                          # NT, RF, VM cleared

    # ------------------------------------------------------------- delivery loop --
    def contributory(x):
        return OR(eq(x, 0), AND(x > 9, x < 14))

    with new("pm_exc", ["vec"]).body() as (b, vec):
        b.set(S.X_DEPTH, 0)
        b.set(S.X_VEC, vec)
        b.set(S.X_ERR, S.FAULT_ERR)
        b.set(S.X_SOFT, S.FAULT_SOFT)
        b.set(S.X_DONE, 0)
        with b.while_not(eq(S.X_DONE, 1)) as w:
            w.call(P.pm_deliver_one, S.X_VEC, S.X_ERR, S.X_SOFT)
            with w.if_(S.FAULT_PEND < 0) as t:
                t.set(S.X_DONE, 1)
            with w.else_() as t:
                t.set(S.X_NV, S.FAULT_PEND)
                t.set(S.X_NE, S.FAULT_ERR)
                t.set(S.FAULT_PEND, -1)
                with t.if_(OR(S.X_DEPTH > 1, eq(S.X_VEC, 8), S.X_NV > 255)) as u:   # triple fault -> shutdown
                    u.set(S.SHUTDOWN, 1)                     # FAULT_VEC keeps the original exception
                    u.set(S.CPU_STATE, 3)
                    u.set(S.X_DONE, 1)
                with t.else_() as u:
                    with u.if_(OR(AND(contributory(S.X_VEC), contributory(S.X_NV)),
                                  AND(eq(S.X_VEC, 14), OR(contributory(S.X_NV), eq(S.X_NV, 14))))) as x:
                        x.set(S.X_NV, 8)
                        x.set(S.X_NE, 0)
                    u.set(S.X_VEC, S.X_NV)
                    u.set(S.X_ERR, S.X_NE)
                    u.set(S.X_SOFT, 0)
                    u.change(S.X_DEPTH, 1)

    # ---------------------------------------------------------- far return / IRET --
    # Same-level or outer-level return.  imm = bytes released after the CS pop (RETF n).
    with new("ret_far_pm", ["is_iret", "imm"]).body() as (b, is_iret, imm):
        b.call(P.pop, S.OSZ)
        b.set(S.R_EIP, S.PV)
        b.call(P.pop, S.OSZ)
        b.set(S.R_CS, S.PV % 65536)
        with b.if_(eq(is_iret, 1)) as t:
            t.call(P.pop, S.OSZ)
            t.set(S.R_EFL, S.PV)
            with t.if_(acc(S.EFL_OTHER, 14)) as u:                  # NT: task return
                u.call(P.cpu_fault, 256)
                u.stop_script()
            with t.if_(AND(eq(S.OSZ, 4), acc(S.R_EFL, 17))) as u:   # return to virtual-8086 mode
                u.call(P.cpu_fault, 256)
                u.stop_script()
        ret_if_fault(b)
        b.set(S.R_RPL, S.R_CS % 4)
        with b.if_(S_lt(S.R_CS, 4)) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        with b.if_(S.R_RPL < S.CPL) as t:
            ferr(t, 13, S.R_CS - S.R_RPL)
            t.stop_script()
        b.call(P.desc_fetch, S.R_CS)
        ret_if_fault(b)
        b.call(P.desc_decode)
        b.set(S.E_CATTR, S.D_ATTR)
        b.set(S.E_CBASE, S.D_BASE)
        b.set(S.E_CLIM, S.D_LIM)
        b.set(S.E_CLIN, S.D_LIN)
        with b.if_(OR(eq(acc(S.E_CATTR, 4), 0), eq(acc(S.E_CATTR, 3), 0))) as t:
            ferr(t, 13, S.R_CS - S.R_RPL)
            t.stop_script()
        b.set(S.X7, floor(S.E_CATTR / 32) % 4)
        with b.if_(eq(acc(S.E_CATTR, 2), 1)) as t:                  # conforming: DPL <= RPL
            with t.if_(S.X7 > S.R_RPL) as u:
                ferr(u, 13, S.R_CS - S.R_RPL)
                u.stop_script()
        with b.else_() as t:
            with t.if_(ne(S.X7, S.R_RPL)) as u:
                ferr(u, 13, S.R_CS - S.R_RPL)
                u.stop_script()
        with b.if_(eq(acc(S.E_CATTR, 7), 0)) as t:
            ferr(t, 11, S.R_CS - S.R_RPL)
            t.stop_script()
        with b.if_(S.R_EIP > S.E_CLIM) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        with b.if_(eq(is_iret, 0)) as t:                            # release RETF n parameters from the old stack
            with t.if_(S.STK32) as u:
                u.replace(S.GPR, 5, (S.GPR[5] + imm) % 4294967296)
            with t.else_() as u:
                u.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + (S.GPR[5] % 65536 + imm) % 65536)
        with b.if_(S.R_RPL > S.CPL) as t:                           # --- outer level: pop SS:ESP, validate SS
            t.call(P.pop, S.OSZ)
            t.set(S.R_ESP, S.PV)
            t.call(P.pop, S.OSZ)
            t.set(S.R_SS, S.PV % 65536)
            ret_if_fault(t)
            with t.if_(S_lt(S.R_SS, 4)) as u:
                u.call(P.cpu_fault_err, 13, 0)
                u.stop_script()
            t.call(P.desc_fetch, S.R_SS)
            ret_if_fault(t)
            t.call(P.desc_decode)
            t.set(S.E_NATTR, S.D_ATTR)
            with t.if_(OR(eq(acc(S.E_NATTR, 4), 0), eq(acc(S.E_NATTR, 3), 1), eq(acc(S.E_NATTR, 1), 0),
                          ne(S.R_SS % 4, S.R_RPL), ne(floor(S.E_NATTR / 32) % 4, S.R_RPL))) as u:
                ferr(u, 13, S.R_SS - S.R_SS % 4)
                u.stop_script()
            with t.if_(eq(acc(S.E_NATTR, 7), 0)) as u:
                ferr(u, 12, S.R_SS - S.R_SS % 4)
                u.stop_script()
            set_accessed(t)
            commit(t, 2, S.R_SS)
            t.set(S.STK32, acc(S.D_ATTR, 10))
            t.replace(S.GPR, 5, S.R_ESP)
            with t.if_(eq(is_iret, 0)) as u:
                with u.if_(S.STK32) as w:
                    w.replace(S.GPR, 5, (S.GPR[5] + imm) % 4294967296)
                with u.else_() as w:
                    w.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + (S.GPR[5] % 65536 + imm) % 65536)
        # flags (IRET): privilege rules use the CPL in force before the return
        with b.if_(eq(is_iret, 1)) as t:
            t.call(P.flags_unpack, S.R_EFL, S.OSZ)
        # commit CS (descriptor values saved before the SS fetch clobbered D_*)
        b.set(S.D_BASE, S.E_CBASE)
        b.set(S.D_LIM, S.E_CLIM)
        b.set(S.D_ATTR, S.E_CATTR)
        b.set(S.D_LIN, S.E_CLIN)
        set_accessed(b)
        commit(b, 1, S.R_CS)
        with b.if_(S.R_RPL > S.CPL) as t:                           # data segments the new level may not use are nulled
            t.set(S.CPL, S.R_RPL)
            t.set(S.X5, 0)
            with t.while_not(eq(S.X5, 6)) as w:
                with w.if_(OR(eq(S.X5, 0), eq(S.X5, 3), eq(S.X5, 4), eq(S.X5, 5))) as x:
                    x.set(S.X6, S.SEGATTR[S.X5 + 1])
                    with x.if_(AND(eq(acc(S.X6, 4), 1), OR(eq(acc(S.X6, 3), 0), eq(acc(S.X6, 2), 0)),
                                   floor(S.X6 / 32) % 4 < S.R_RPL)) as y:
                        y.replace(S.SEG, S.X5 + 1, 0)
                        y.replace(S.SEGPERM, S.X5 + 1, 0)
                        y.replace(S.SEGBASE, S.X5 + 1, 0)
                        y.replace(S.SEGLIM, S.X5 + 1, 0)
                        y.replace(S.SEGATTR, S.X5 + 1, 0)
                w.change(S.X5, 1)
        b.set(S.DEF_OSZ, 2 + 2 * acc(S.E_CATTR, 10))
        b.set(S.DEF_ASZ, 2 + 2 * acc(S.E_CATTR, 10))
        b.set(S.CODE_MASK, 65536 + 4294901760 * acc(S.E_CATTR, 10))
        b.set(S.EIP, S.R_EIP)

    # --------------------------------------------------------------- I/O permission --
    with new("io_check", ["port", "size"]).body() as (b, port, size):
        with b.if_(AND(eq(S.CR0 % 2, 1), S.CPL > floor(S.EFL_OTHER / 4096) % 4)) as t:
            t.set(S.SUPACC, 1)
            # TSS I/O bitmap (32-bit TSS only): word at +0x66 gives the bitmap offset
            with t.if_(floor(S.TR_ATTR % 16 / 8) < 1) as u:
                u.call(P.cpu_fault_err, 13, 0)
                u.stop_script()
            t.call(mem.mem_rw, (S.TR_BASE + 102) % 4294967296)
            t.set(S.X1, S.MEMV)
            t.set(S.X2, 0)                                           # any denied bit among the `size` ports
            t.set(S.X3, 0)
            with t.while_not(eq(S.X3, size)) as w:
                w.set(S.X4, S.X1 + floor((port + S.X3) / 8))
                with w.if_(S.X4 + 1 > S.TR_LIM) as x:
                    x.set(S.X2, 1)
                with w.else_() as x:
                    x.call(mem.mem_rb, (S.TR_BASE + S.X4) % 4294967296)
                    x.set(S.X5, floor(S.MEMV / S.POW2[(port + S.X3) % 8 + 1]) % 2)
                    with x.if_(eq(S.X5, 1)) as y:
                        y.set(S.X2, 1)
                w.change(S.X3, 1)
            t.set(S.SUPACC, 0)
            with t.if_(eq(S.X2, 1)) as u:
                u.call(P.cpu_fault_err, 13, 0)


def S_lt(a, n):
    return a < n
