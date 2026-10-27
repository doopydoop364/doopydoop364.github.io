"""x86 CPU core (Scratch blocks generated via sb3gen).

Everything here is *generator* code: it runs at build time and emits Scratch
blocks.  The emitted blocks fetch, decode and execute genuine x86 machine code
from the emulated memory (see src/memory/memory.py).

Layers (bottom-up):
    registers   reg_get / reg_set / seg_load
    fetch       fetch8 / fetch_imm
    stack       push / pop
    modrm       modrm_decode / rm_get / rm_set  (16- and 32-bit addressing)
    alu         alu(op, a, b)  + flags pack/unpack + condition codes
    handlers    one procedure per opcode family
    step        prefix loop + opcode dispatch (cpu_step)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def sx8(x):
    """Sign-extend an 8-bit value (0..255) to a Scratch number (-128..127)."""
    return x - 256 * floor(x / 128)


def sx16(x):
    return x - 65536 * floor(x / 32768)


class Procs:
    pass


def build(p, S, mem):
    P = Procs()

    def new(name, args=()):
        proc = p.procedure(name, args)
        setattr(P, name, proc)
        return proc

    def set_size(b, sz):
        """Inline: OPSZ / OP_M / OP_SGN for a byte width (const or expression)."""
        if isinstance(sz, int):
            b.set(S.OPSZ, sz)
            b.set(S.OP_M, [256, 65536, 16777216, 4294967296][sz - 1])
            b.set(S.OP_SGN, [128, 32768, 8388608, 2147483648][sz - 1])
        else:
            b.set(S.OPSZ, sz)
            b.set(S.OP_M, S.SZ_M[sz])
            b.set(S.OP_SGN, S.SZ_SGN[sz])

    def limit_chk(b, size):
        """Segment-limit check for EA_SEG:EA_OFF (#SS for SS, else #GP); returns from the caller."""
        with b.if_(S.EA_OFF + size - 1 > S.SEGLIM[S.EA_SEG + 1]) as t:
            with t.if_(eq(S.EA_SEG, 2)) as u:
                u.call(P.cpu_fault, 12)
            with t.else_() as u:
                u.call(P.cpu_fault, 13)
            t.stop_script()

    # ======================================================================
    # registers
    # ======================================================================
    pr = new("reg_get", ["size", "idx"])
    with pr.body() as (b, size, idx):
        with b.if_(eq(size, 4)) as t:
            t.set(S.RV, S.GPR[idx + 1])
        with b.else_() as t:
            with t.if_(eq(size, 2)) as u:
                u.set(S.RV, S.GPR[idx + 1] % 65536)
            with t.else_() as u:
                with u.if_(idx < 4) as w:
                    w.set(S.RV, S.GPR[idx + 1] % 256)
                with u.else_() as w:
                    w.set(S.RV, floor(S.GPR[idx - 3] / 256) % 256)

    pr = new("reg_set", ["size", "idx", "val"])
    with pr.body() as (b, size, idx, val):
        with b.if_(eq(size, 4)) as t:
            t.replace(S.GPR, idx + 1, val)
        with b.else_() as t:
            with t.if_(eq(size, 2)) as u:
                u.set(S.TC, S.GPR[idx + 1])
                u.replace(S.GPR, idx + 1, S.TC - S.TC % 65536 + val)
            with t.else_() as u:
                with u.if_(idx < 4) as w:
                    w.set(S.TC, S.GPR[idx + 1])
                    w.replace(S.GPR, idx + 1, S.TC - S.TC % 256 + val)
                with u.else_() as w:
                    w.set(S.TC, S.GPR[idx - 3])
                    w.replace(S.GPR, idx - 3, S.TC - (floor(S.TC / 256) % 256) * 256 + val * 256)

    pr = new("seg_load", ["idx", "sel"])
    with pr.body() as (b, idx, sel):
        # Real-mode load: base = selector * 16.  Protected-mode descriptor
        # loading is a later milestone (v0.6); the hidden-base list exists so
        # that it can slot in here.
        b.replace(S.SEG, idx + 1, sel)
        b.replace(S.SEGBASE, idx + 1, sel * 16)
        b.replace(S.SEGLIM, idx + 1, 65535)

    # ======================================================================
    # fetch
    # ======================================================================
    pr = new("fetch8")
    with pr.body() as (b,):
        b.set(S.FLIN, (S.SEGBASE[2] + S.EIP) % 4294967296)
        b.call(mem.mem_fb, S.FLIN)
        b.set(S.FB, S.MEMV)
        b.set(S.EIP, (S.EIP + 1) % S.CODE_MASK)

    pr = new("fetch_imm", ["size"])
    with pr.body() as (b, size):
        b.call(P.fetch8)
        b.set(S.IMM, S.FB)
        with b.if_(ge(size, 2)) as t:
            t.call(P.fetch8)
            t.set(S.IMM, S.IMM + S.FB * 256)
            with t.if_(eq(size, 4)) as u:
                u.call(P.fetch8)
                u.set(S.IMM, S.IMM + S.FB * 65536)
                u.call(P.fetch8)
                u.set(S.IMM, S.IMM + S.FB * 16777216)

    # ======================================================================
    # faults (v0.1: stop the CPU with a diagnostic; vectoring arrives in v0.2)
    # ======================================================================
    # Convention: a faulting helper calls cpu_fault and returns early (stop this
    # script).  Callers re-check FAULT_PEND before modifying architectural state;
    # cpu_step delivers the pending exception after the handler returns
    # (control.py: exc_deliver).  The first fault of an instruction wins.
    pr = new("cpu_fault", ["vec"])
    with pr.body() as (b, vec):
        with b.if_(S.FAULT_PEND < 0) as t:
            t.set(S.FAULT_PEND, vec)
            t.set(S.FAULT_ERR, 0)
            t.set(S.FAULT_TRAP, 0)

    pr = new("cpu_trap", ["vec"])      # software interrupt / trap: pushes the *next* EIP
    with pr.body() as (b, vec):
        with b.if_(S.FAULT_PEND < 0) as t:
            t.set(S.FAULT_PEND, vec)
            t.set(S.FAULT_ERR, 0)
            t.set(S.FAULT_TRAP, 1)

    # ======================================================================
    # stack
    # ======================================================================
    pr = new("push", ["size", "val"])
    with pr.body() as (b, size, val):
        with b.if_(S.STK32) as t:
            t.set(S.SPW, (S.GPR[5] - size + 4294967296) % 4294967296)
            t.replace(S.GPR, 5, S.SPW)
        with b.else_() as t:
            t.set(S.SPW, (S.GPR[5] % 65536 - size + 65536) % 65536)
            t.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + S.SPW)
        b.set(S.TD, (S.SEGBASE[3] + S.SPW) % 4294967296)
        with b.if_(eq(size, 2)) as t:
            t.call(mem.mem_ww, S.TD, val)
        with b.else_() as t:
            t.call(mem.mem_wd, S.TD, val)

    pr = new("pop", ["size"])
    with pr.body() as (b, size):
        with b.if_(S.STK32) as t:
            t.set(S.SPW, S.GPR[5])
        with b.else_() as t:
            t.set(S.SPW, S.GPR[5] % 65536)
        b.set(S.TD, (S.SEGBASE[3] + S.SPW) % 4294967296)
        with b.if_(eq(size, 2)) as t:
            t.call(mem.mem_rw, S.TD)
        with b.else_() as t:
            t.call(mem.mem_rd, S.TD)
        b.set(S.PV, S.MEMV)
        with b.if_(S.STK32) as t:
            t.replace(S.GPR, 5, (S.SPW + size) % 4294967296)
        with b.else_() as t:
            t.replace(S.GPR, 5, S.GPR[5] - S.SPW + (S.SPW + size) % 65536)

    # ======================================================================
    # ModR/M + effective addresses
    # ======================================================================
    pr = new("ea_seg_select", ["default_seg"])
    with pr.body() as (b, dseg):
        with b.if_(S.PFX_SEG < 0) as t:
            t.set(S.EA_SEG, dseg)
        with b.else_() as t:
            t.set(S.EA_SEG, S.PFX_SEG)

    pr = new("ea16")
    with pr.body() as (b,):
        with b.if_(AND(eq(S.MOD, 0), eq(S.RM, 6))) as t:
            t.call(P.fetch_imm, 2)
            t.set(S.EA_OFF, S.IMM)
            t.call(P.ea_seg_select, 3)
        with b.else_() as t:
            t.set(S.EA_OFF, S.GPR[S.EA16_B1[S.RM + 1]] % 65536 + S.GPR[S.EA16_B2[S.RM + 1]] % 65536)
            with t.if_(eq(S.MOD, 1)) as u:
                u.call(P.fetch8)
                u.set(S.EA_OFF, S.EA_OFF + sx8(S.FB))
            with t.if_(eq(S.MOD, 2)) as u:
                u.call(P.fetch_imm, 2)
                u.set(S.EA_OFF, S.EA_OFF + S.IMM)
            t.set(S.EA_OFF, (S.EA_OFF + 65536) % 65536)
            t.call(P.ea_seg_select, S.EA16_SEG[S.RM + 1])

    pr = new("ea32")
    with pr.body() as (b,):
        b.set(S.TA, 3)                      # default segment: DS
        with b.if_(eq(S.RM, 4)) as t:       # SIB follows
            t.call(P.fetch8)
            t.set(S.SIB, S.FB)
            # base register (or disp32 when mod=0 and base=5)
            with t.if_(AND(eq(S.MOD, 0), eq(S.SIB % 8, 5))) as u:
                u.call(P.fetch_imm, 4)
                u.set(S.EA_OFF, S.IMM)
            with t.else_() as u:
                u.set(S.EA_OFF, S.GPR[S.SIB % 8 + 1])
                with u.if_(OR(eq(S.SIB % 8, 4), eq(S.SIB % 8, 5))) as w:
                    w.set(S.TA, 2)          # ESP/EBP based -> SS
            # scaled index (index=4 means none)
            with t.if_(ne(floor(S.SIB / 8) % 8, 4)) as u:
                u.set(S.EA_OFF, S.EA_OFF + S.GPR[floor(S.SIB / 8) % 8 + 1] * S.SCALE[floor(S.SIB / 64) + 1])
        with b.else_() as t:
            with t.if_(AND(eq(S.MOD, 0), eq(S.RM, 5))) as u:
                u.call(P.fetch_imm, 4)
                u.set(S.EA_OFF, S.IMM)
            with t.else_() as u:
                u.set(S.EA_OFF, S.GPR[S.RM + 1])
                with u.if_(eq(S.RM, 5)) as w:
                    w.set(S.TA, 2)
        with b.if_(eq(S.MOD, 1)) as t:
            t.call(P.fetch8)
            t.set(S.EA_OFF, S.EA_OFF + sx8(S.FB))
        with b.if_(eq(S.MOD, 2)) as t:
            t.call(P.fetch_imm, 4)
            t.set(S.EA_OFF, S.EA_OFF + S.IMM)
        b.set(S.EA_OFF, (S.EA_OFF + 4294967296) % 4294967296)
        b.call(P.ea_seg_select, S.TA)

    pr = new("modrm_decode")
    with pr.body() as (b,):
        b.call(P.fetch8)
        b.set(S.MODRM, S.FB)
        b.set(S.MOD, floor(S.FB / 64))
        b.set(S.REG, floor(S.FB / 8) % 8)
        b.set(S.RM, S.FB % 8)
        with b.if_(eq(S.MOD, 3)) as t:
            t.set(S.EA_REG, 1)
        with b.else_() as t:
            t.set(S.EA_REG, 0)
            with t.if_(eq(S.ASZ, 2)) as u:
                u.call(P.ea16)
            with t.else_() as u:
                u.call(P.ea32)
            t.set(S.EA_LIN, (S.SEGBASE[S.EA_SEG + 1] + S.EA_OFF) % 4294967296)
            with t.if_(S.DIAG_MEM) as u:
                u.set(S.LAST_EA_SEG, S.SEG[S.EA_SEG + 1])
                u.set(S.LAST_EA_OFF, S.EA_OFF)

    pr = new("rm_get", ["size"])
    with pr.body() as (b, size):
        with b.if_(S.EA_REG) as t:
            t.call(P.reg_get, size, S.RM)
            t.set(S.RMV, S.RV)
        with b.else_() as t:
            limit_chk(t, size)
            with t.if_(eq(size, 1)) as u:
                u.call(mem.mem_rb, S.EA_LIN)
            with t.else_() as u:
                with u.if_(eq(size, 2)) as w:
                    w.call(mem.mem_rw, S.EA_LIN)
                with u.else_() as w:
                    w.call(mem.mem_rd, S.EA_LIN)
            t.set(S.RMV, S.MEMV)

    pr = new("rm_set", ["size", "val"])
    with pr.body() as (b, size, val):
        with b.if_(S.EA_REG) as t:
            t.call(P.reg_set, size, S.RM, val)
        with b.else_() as t:
            limit_chk(t, size)
            with t.if_(eq(size, 1)) as u:
                u.call(mem.mem_wb, S.EA_LIN, val)
            with t.else_() as u:
                with u.if_(eq(size, 2)) as w:
                    w.call(mem.mem_ww, S.EA_LIN, val)
                with u.else_() as w:
                    w.call(mem.mem_wd, S.EA_LIN, val)

    # ======================================================================
    # flags
    # ======================================================================
    pr = new("flags_pack")
    with pr.body() as (b,):
        b.set(S.EFL, S.CF + 2 + S.PF * 4 + S.AF * 16 + S.ZF * 64 + S.SF * 128 + S.TF * 256 +
              S.IF * 512 + S.DF * 1024 + S.OF * 2048 + S.EFL_OTHER)

    pr = new("flags_unpack", ["val", "size"])
    with pr.body() as (b, val, size):
        b.set(S.CF, val % 2)
        b.set(S.PF, floor(val / 4) % 2)
        b.set(S.AF, floor(val / 16) % 2)
        b.set(S.ZF, floor(val / 64) % 2)
        b.set(S.SF, floor(val / 128) % 2)
        b.set(S.TF, floor(val / 256) % 2)
        b.set(S.IF, floor(val / 512) % 2)
        b.set(S.DF, floor(val / 1024) % 2)
        b.set(S.OF, floor(val / 2048) % 2)
        # bits 12-15 (IOPL, NT) are writable; VM(17)/RF(16) are preserved by POPF.
        with b.if_(eq(size, 2)) as t:
            t.set(S.EFL_OTHER, S.EFL_OTHER - S.EFL_OTHER % 65536 + floor(val / 4096) % 16 * 4096)
        with b.else_() as t:
            t.set(S.EFL_OTHER, floor(S.EFL_OTHER / 65536) % 4 * 65536 + floor(val / 4096) % 16 * 4096 +
                  floor(val / 262144) * 262144)

    pr = new("cond_eval", ["cc"])
    with pr.body() as (b, cc):
        b.set(S.TC, floor(cc / 2))
        b.dispatch(S.TC, [
            (0, 0, lambda t: t.set(S.COND, S.OF)),
            (1, 1, lambda t: t.set(S.COND, S.CF)),
            (2, 2, lambda t: t.set(S.COND, S.ZF)),
            (3, 3, lambda t: t.set(S.COND, floor((S.CF + S.ZF + 1) / 2))),
            (4, 4, lambda t: t.set(S.COND, S.SF)),
            (5, 5, lambda t: t.set(S.COND, S.PF)),
            (6, 6, lambda t: t.set(S.COND, abs_(S.SF - S.OF))),
            (7, 7, lambda t: t.set(S.COND, floor((S.ZF + abs_(S.SF - S.OF) + 1) / 2))),
        ])
        with b.if_(eq(cc % 2, 1)) as t:
            t.set(S.COND, 1 - S.COND)

    # ======================================================================
    # ALU: op 0 ADD 1 OR 2 ADC 3 SBB 4 AND 5 SUB 6 XOR 7 CMP
    # operands a, b are unsigned values of width OPSZ; result in ALU_R
    # ======================================================================
    S.ALU_CLASS = p.list("ALU class by op (0=add 1=sub 2=logic)", [0, 2, 0, 1, 2, 1, 2, 1])
    pr = new("alu", ["op", "a", "b"])
    with pr.body() as (b, op, a, bb):
        b.set(S.TA, S.ALU_CLASS[op + 1])
        with b.if_(eq(S.TA, 0)) as t:                      # ADD / ADC
            t.set(S.TB, 0)
            with t.if_(eq(op, 2)) as u:
                u.set(S.TB, S.CF)
            t.set(S.TC, a + bb + S.TB)
            t.set(S.ALU_R, S.TC % S.OP_M)
            t.set(S.CF, floor(S.TC / S.OP_M))
            t.set(S.AF, floor((a % 16 + bb % 16 + S.TB) / 16))
            t.set(S.OF, (1 - abs_(floor(a / S.OP_SGN) - floor(bb / S.OP_SGN))) *
                  abs_(floor(S.ALU_R / S.OP_SGN) - floor(a / S.OP_SGN)))
        with b.else_() as t:
            with t.if_(eq(S.TA, 1)) as u:                  # SUB / SBB / CMP
                u.set(S.TB, 0)
                with u.if_(eq(op, 3)) as w:
                    w.set(S.TB, S.CF)
                u.set(S.TC, a - bb - S.TB)
                u.set(S.ALU_R, (S.TC + S.OP_M) % S.OP_M)
                u.set(S.CF, 0 - floor(S.TC / S.OP_M))
                u.set(S.AF, 0 - floor((a % 16 - bb % 16 - S.TB) / 16))
                u.set(S.OF, abs_(floor(a / S.OP_SGN) - floor(bb / S.OP_SGN)) *
                      abs_(floor(S.ALU_R / S.OP_SGN) - floor(a / S.OP_SGN)))
            with t.else_() as u:                           # AND / OR / XOR
                # bytewise AND through the 256x256 table; OR/XOR derive from it:
                #   a + b = (a XOR b) + 2*(a AND b),  a OR b = a + b - (a AND b)
                with u.if_(eq(S.OPSZ, 1)) as w:
                    w.set(S.ANDR, S.ANDTAB[a * 256 + bb + 1])
                with u.else_() as w:
                    w.set(S.ANDR, S.ANDTAB[(a % 256) * 256 + bb % 256 + 1] +
                          256 * S.ANDTAB[(floor(a / 256) % 256) * 256 + floor(bb / 256) % 256 + 1])
                    with w.if_(eq(S.OPSZ, 4)) as x:
                        x.set(S.ANDR, S.ANDR +
                              65536 * S.ANDTAB[(floor(a / 65536) % 256) * 256 + floor(bb / 65536) % 256 + 1] +
                              16777216 * S.ANDTAB[floor(a / 16777216) * 256 + floor(bb / 16777216) + 1])
                with u.if_(eq(op, 4)) as w:
                    w.set(S.ALU_R, S.ANDR)
                with u.if_(eq(op, 1)) as w:
                    w.set(S.ALU_R, a + bb - S.ANDR)
                with u.if_(eq(op, 6)) as w:
                    w.set(S.ALU_R, a + bb - 2 * S.ANDR)
                u.set(S.CF, 0)
                u.set(S.OF, 0)
                u.set(S.AF, 0)         # architecturally undefined; fixed to 0
        b.set(S.ZF, 1 - mathop("ceiling", S.ALU_R / S.OP_M))
        b.set(S.SF, floor(S.ALU_R / S.OP_SGN))
        b.set(S.PF, S.PAR[S.ALU_R % 256 + 1])

    # inc/dec leave CF untouched
    pr = new("incdec", ["is_dec", "a"])
    with pr.body() as (b, is_dec, a):
        b.set(S.TD, S.CF)
        with b.if_(eq(is_dec, 1)) as t:
            t.call(P.alu, 5, a, 1)
        with b.else_() as t:
            t.call(P.alu, 0, a, 1)
        b.set(S.CF, S.TD)

    _handlers(p, S, mem, P, new, set_size)
    return P


def abs_(x):
    return mathop("abs", x)


def _handlers(p, S, mem, P, new, set_size):
    from cpu_handlers import build_handlers  # noqa
    build_handlers(p, S, mem, P, new, set_size)
