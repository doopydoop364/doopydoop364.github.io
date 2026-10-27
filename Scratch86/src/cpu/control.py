"""CPU control: instruction step, batch runner, reset, boot-image loader.

    cpu_step            fetch prefixes + opcode, dispatch to a handler
    cpu_run_batch(n)    run up to n instructions (stops on halt/fault/request)
    cpu_reset           architectural CPU reset state
    machine_boot_test   documented v0.1 test configuration (see docs/cpu.md)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def build(p, S, mem, P):
    def new(name, args=()):
        proc = p.procedure(name, args)
        setattr(P, name, proc)
        return proc

    # ---------------------------------------------------- exception delivery --
    # Real-mode delivery through the IVT (vector*4: IP, CS).  Faults restart the
    # instruction (EIP_START), traps / INT n resume after it.  Pushes are always
    # 16-bit in real mode.  A fault while pushing is a double fault -> shutdown.
    pr = new("exc_deliver")
    with pr.body() as (b,):
        b.set(S.H4, S.FAULT_PEND)              # vector
        b.set(S.FAULT_PEND, -1)
        with b.if_(S.H4 > 255) as t:           # 256: feature not implemented yet -> visible stop
            t.set(S.FAULT_VEC, 256)
            t.set(S.FAULT_OPC, S.OPCODE)
            t.set(S.FAULT_EIP, S.EIP_START)
            t.set(S.EIP, S.EIP_START)
            t.set(S.CPU_STATE, 3)
            t.stop_script()
        b.set(S.FAULT_VEC, S.H4)
        b.set(S.FAULT_ERR1, S.FAULT_ERR)
        b.set(S.FAULT_OPC, S.OPCODE)
        b.set(S.FAULT_EIP, S.EIP_START)
        with b.if_(eq(S.FAULT_TRAP, 0)) as t:
            t.set(S.EIP, S.EIP_START)
            t.replace(S.GPR, 5, S.ESP_START)   # a faulting instruction leaves no partial stack effects
        with b.if_(eq(S.CR0 % 2, 1)) as t:     # protected mode: IDT gates, privilege switch, #DF rules
            t.call(P.pm_exc, S.H4)
            t.stop_script()
        b.call(mem.mem_rw, S.H4 * 4)
        b.set(S.H3, S.MEMV)                    # new IP
        b.call(mem.mem_rw, S.H4 * 4 + 2)
        b.set(S.H2, S.MEMV)                    # new CS
        with b.if_(AND(S.STOP_UNSET, eq(S.H3, 0), eq(S.H2, 0))) as t:
            t.set(S.CPU_STATE, 3)              # debugger-style stop: no handler installed
            t.stop_script()
        b.call(P.flags_pack)
        b.set(S.H1, S.EIP % 65536)             # saved IP (push helpers clobber TA..TD only)
        b.call(P.push, 2, S.EFL % 65536)
        b.call(P.push, 2, S.SEG[2])
        b.call(P.push, 2, S.H1)
        with b.if_(S.FAULT_PEND > -1) as t:    # fault while delivering: shutdown
            t.set(S.FAULT_PEND, -1)
            t.set(S.CPU_STATE, 3)
            t.stop_script()
        b.set(S.IF, 0)
        b.set(S.TF, 0)
        b.call(P.seg_load, 1, S.H2)
        b.set(S.EIP, S.H3)

    # ---------------------------------------------------------------- step --
    pr = new("cpu_step")
    with pr.body() as (b,):
        b.set(S.EIP_START, S.EIP)
        b.set(S.TF_START, S.TF)
        b.set(S.ESP_START, S.GPR[5])
        b.set(S.LAST_FETCH, (S.SEGBASE[2] + S.EIP) % 4294967296)
        b.set(S.PFX_SEG, -1)
        b.set(S.PFX_REP, 0)
        b.set(S.PFX_LOCK, 0)
        b.set(S.PFX_COUNT, 0)
        b.set(S.OSZ, S.DEF_OSZ)
        b.set(S.ASZ, S.DEF_ASZ)
        b.set(S.PFX_MORE, 1)
        with b.while_not(eq(S.PFX_MORE, 0)) as w:
            w.call(P.fetch8)
            w.set(S.OPCODE, S.FB)
            w.set(S.H1, S.PFXKIND[S.OPCODE + 1])
            with w.if_(eq(S.H1, 0)) as t:
                t.set(S.PFX_MORE, 0)
            with w.else_() as t:
                t.change(S.PFX_COUNT, 1)
                t.dispatch(S.H1, [
                    (1, 1, lambda u: u.set(S.PFX_SEG, S.PFXSEG[S.OPCODE + 1])),
                    (2, 2, lambda u: u.set(S.OSZ, 6 - S.OSZ)),
                    (3, 3, lambda u: u.set(S.ASZ, 6 - S.ASZ)),
                    (4, 4, lambda u: u.set(S.PFX_LOCK, 1)),
                    (5, 5, lambda u: u.set(S.PFX_REP, 1)),
                    (6, 6, lambda u: u.set(S.PFX_REP, 2)),
                ])
                with t.if_(S.PFX_COUNT > 14) as u:
                    u.call(P.cpu_fault, 6)
                    u.set(S.PFX_MORE, 0)
        with b.if_(eq(S.CPU_STATE, 1)) as t:
            t.set(S.LAST_OPCODE, S.OPCODE)
            with t.if_(S.DIAG_TRACE) as u:          # ring buffer of the last 8 instructions
                u.replace(S.TRACE_CS, S.TRACE_POS + 1, S.SEG[2])
                u.replace(S.TRACE_IP, S.TRACE_POS + 1, S.EIP_START)
                u.replace(S.TRACE_OP, S.TRACE_POS + 1, S.OPCODE)
                u.set(S.TRACE_POS, (S.TRACE_POS + 1) % 8)
            cases = [(lo, hi, (lambda u, nm=nm: u.call(getattr(P, nm)))) for lo, hi, nm in S.DISPATCH]
            t.dispatch(S.OPCODE, cases, default=lambda u: u.call(P.h_unsupported))
            with t.if_(AND(ne(S.CPU_STATE, 3), OR(S.FAULT_PEND < 0, eq(S.FAULT_TRAP, 1)))) as u:
                u.change(S.INSN_COUNT, 1)      # faulting instructions do not retire
            with t.if_(AND(eq(S.TF_START, 1), S.FAULT_PEND < 0, eq(S.CPU_STATE, 1))) as u:
                u.call(P.cpu_trap, 1, 0)          # single-step trap after the instruction
        with b.if_(S.FAULT_PEND > -1) as t:
            t.call(P.exc_deliver)

    pr = new("cpu_run_batch", ["n"])
    with pr.body() as (b, n):
        b.set(S.BATCH_LEFT, n)
        with b.while_not(OR(S.BATCH_LEFT < 1, ne(S.CPU_STATE, 1), ne(S.REQ, 0))) as w:
            w.call(P.cpu_step)
            w.change(S.BATCH_LEFT, -1)

    # --------------------------------------------------------------- reset --
    pr = new("cpu_reset")
    with pr.body() as (b,):
        for i in range(1, 9):
            b.replace(S.GPR, i, 0)
        b.replace(S.GPR, 9, 0)
        b.replace(S.GPR, 3, 0x303)          # EDX: 386 processor signature
        for f in ("CF", "PF", "AF", "ZF", "SF", "TF", "IF", "DF", "OF"):
            b.set(getattr(S, f), 0)
        b.set(S.EFL_OTHER, 0)
        b.set(S.CR0, 0x60000010)
        b.set(S.CR2, 0)
        b.set(S.CR3, 0)
        b.set(S.CPU_MODE, 0)
        b.set(S.CPL, 0)
        b.set(S.CR4, 0)
        b.set(S.GDTR_BASE, 0)
        b.set(S.GDTR_LIM, 65535)
        b.set(S.IDTR_BASE, 0)
        b.set(S.IDTR_LIM, 1023)
        for nm in ("LDTR_SEL", "LDTR_BASE", "LDTR_LIM", "LDTR_ATTR", "TR_SEL", "TR_BASE", "TR_LIM", "TR_ATTR"):
            b.set(getattr(S, nm), 0)
        b.set(S.TR_LIM, 65535)
        for i in range(6):                  # real-mode descriptor caches: 64 KiB, read/write data (CS: code)
            b.replace(S.SEGLIM, i + 1, 65535)
            b.replace(S.SEGATTR, i + 1, 0x9B if i == 1 else 0x93)
            b.replace(S.SEGPERM, i + 1, 7)
        b.set(S.DEF_OSZ, 2)
        b.set(S.DEF_ASZ, 2)
        b.set(S.STK32, 0)
        b.set(S.CODE_MASK, 65536)
        for i in range(6):
            b.replace(S.SEG, i + 1, 0)
            b.replace(S.SEGBASE, i + 1, 0)
        b.replace(S.SEG, 2, 0xF000)         # CS:EIP = F000:FFF0, base FFFF0000
        b.replace(S.SEGBASE, 2, 0xFFFF0000)
        b.set(S.EIP, 0xFFF0)
        b.set(S.CPU_STATE, 1)
        b.set(S.FAULT_VEC, 0)
        b.set(S.FAULT_PEND, -1)
        b.set(S.SHUTDOWN, 0)
        b.set(S.INSN_COUNT, 0)
        b.set(S.PFX_SEG, -1)
        b.set(S.LAST_FETCH, 0)
        b.set(S.LAST_OPCODE, 0)
        b.set(S.LAST_RD_LIN, 0)
        b.set(S.LAST_RD_PHYS, 0)
        b.set(S.LAST_WR_LIN, 0)
        b.set(S.LAST_WR_PHYS, 0)
        b.set(S.LAST_MEM_VAL, 0)
        b.set(S.LAST_MEM_W, 0)
        b.set(S.LAST_MEM_RW, 0)

    pr = new("machine_boot_test")
    with pr.body() as (b,):
        b.call(P.cpu_reset)
        # v0.1 controlled configuration: flat 0000:xxxx real mode, image at 0000:7C00.
        # (The architectural reset vector F000:FFF0 needs a BIOS ROM; see roadmap v0.3.)
        for i in range(6):
            b.call(P.seg_load, i, 0)
        b.replace(S.GPR, 5, 0x7C00)         # ESP
        b.set(S.EIP, 0x7C00)
        b.set(S.H1, 0)
        with b.while_not(eq(S.H1, S.BOOT_IMAGE.length())) as w:
            w.call(mem.mem_wb, 0x7C00 + S.H1, S.BOOT_IMAGE[S.H1 + 1])
            w.change(S.H1, 1)
    return P
