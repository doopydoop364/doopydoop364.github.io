"""Multiply / divide / shift / rotate helpers and their handlers (generator code).

64-bit intermediates: Scratch numbers are doubles, exact only to 2^53, so 32-bit
MUL and DIV work on 16-bit limbs (see umul / udivp).

Helper procedures (operand width is the current OPSZ / OP_M / OP_SGN):
    umul(a, b)          -> ML, MH           unsigned a*b as a 2N-bit value
    smul(a, b)          -> ML, MH, MFIT     signed multiply (a, b are N-bit patterns)
    udivp(hi, lo, d)    -> QQ, RR           unsigned (hi:lo)/d, caller checked hi < d
    shiftp(op, v, cnt)  -> ALU_R + CF/OF/SF/ZF/PF   (op 0 ROL 1 ROR 2 RCL 3 RCR 4 SHL 5 SHR 6 SAL 7 SAR)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def abs_(x):
    return mathop("abs", x)


def build_math(p, S, mem, P, new, set_size, fchk):
    N = lambda: S.OPSZ * 8   # noqa: E731

    def P2(k):
        return S.POW2[k + 1]

    def szf(b):
        """SF ZF PF from ALU_R at the current width."""
        b.set(S.ZF, 1 - mathop("ceiling", S.ALU_R / S.OP_M))
        b.set(S.SF, floor(S.ALU_R / S.OP_SGN))
        b.set(S.PF, S.PAR[S.ALU_R % 256 + 1])

    # ---------------------------------------------------------------- umul --
    pr = new("umul", ["a", "b"])
    with pr.body() as (b, a, bb):
        with b.if_(S.OPSZ < 4) as t:
            t.set(S.X1, a * bb)
            t.set(S.ML, S.X1 % S.OP_M)
            t.set(S.MH, floor(S.X1 / S.OP_M))
        with b.else_() as t:
            t.set(S.X1, a % 65536)                       # a0
            t.set(S.X2, floor(a / 65536))                # a1
            t.set(S.X3, bb % 65536)                      # b0
            t.set(S.X4, floor(bb / 65536))               # b1
            t.set(S.X5, S.X1 * S.X4 + S.X2 * S.X3)       # mid (< 2^33)
            t.set(S.X6, S.X1 * S.X3 + (S.X5 % 65536) * 65536)   # low part incl. carry (< 2^33)
            t.set(S.ML, S.X6 % 4294967296)
            t.set(S.MH, S.X2 * S.X4 + floor(S.X5 / 65536) + floor(S.X6 / 4294967296))

    pr = new("smul", ["a", "b"])
    with pr.body() as (b, a, bb):
        b.set(S.X7, a - S.OP_M * floor(a / S.OP_SGN))      # signed a
        b.set(S.X8, bb - S.OP_M * floor(bb / S.OP_SGN))    # signed b
        b.set(S.TD, abs_(floor(a / S.OP_SGN) - floor(bb / S.OP_SGN)))   # 1 if the product is negative
        b.call(P.umul, abs_(S.X7), abs_(S.X8))
        with b.if_(eq(S.TD, 1)) as t:                      # two's complement negate of ML:MH
            t.set(S.X1, S.ML)
            t.set(S.ML, (S.OP_M - S.ML) % S.OP_M)
            t.set(S.MH, (S.OP_M - 1 - S.MH + 1 - mathop("ceiling", S.X1 / S.OP_M)) % S.OP_M)
        b.set(S.MFIT, 0)
        with b.if_(OR(AND(eq(S.MH, 0), S.ML < S.OP_SGN), AND(eq(S.MH, S.OP_M - 1), ge(S.ML, S.OP_SGN)))) as t:
            t.set(S.MFIT, 1)

    # ---------------------------------------------------------------- udiv --
    pr = new("udivp", ["hi", "lo", "d"])
    with pr.body() as (b, hi, lo, d):
        with b.if_(S.OPSZ < 4) as t:
            t.set(S.X1, hi * S.OP_M + lo)
            t.set(S.QQ, floor(S.X1 / d))
            t.set(S.RR, S.X1 - S.QQ * d)
        with b.else_() as t:
            t.set(S.X1, hi * 65536 + floor(lo / 65536))
            t.set(S.X2, floor(S.X1 / d))
            t.set(S.X3, S.X1 - S.X2 * d)
            t.set(S.X1, S.X3 * 65536 + lo % 65536)
            t.set(S.X4, floor(S.X1 / d))
            t.set(S.RR, S.X1 - S.X4 * d)
            t.set(S.QQ, S.X2 * 65536 + S.X4)

    # ------------------------------------------------------------ MUL / DIV --
    def mul_store(b):
        with b.if_(eq(S.OPSZ, 1)) as t:
            t.call(P.reg_set, 2, 0, S.MH * 256 + S.ML)
        with b.else_() as t:
            t.call(P.reg_set, S.OPSZ, 0, S.ML)
            t.call(P.reg_set, S.OPSZ, 2, S.MH)

    def mul_flags(b, cf):
        b.set(S.CF, cf)
        b.set(S.OF, cf)
        b.set(S.ALU_R, S.ML)
        szf(b)
        b.set(S.AF, 0)

    pr = new("grp3_muldiv")           # F6/F7 /4../7; modrm decoded, OPSZ set
    with pr.body() as (b,):
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        with b.if_(S.REG < 6) as t:                          # MUL / IMUL
            t.call(P.reg_get, S.OPSZ, 0)
            with t.if_(eq(S.REG, 4)) as u:
                u.call(P.umul, S.RV, S.H3)
                mul_flags(u, mathop("ceiling", S.MH / S.OP_M))
            with t.else_() as u:
                u.call(P.smul, S.RV, S.H3)
                mul_flags(u, 1 - S.MFIT)
            mul_store(t)
        with b.else_() as t:                                 # DIV / IDIV
            with t.if_(eq(S.OPSZ, 1)) as u:
                u.call(P.reg_get, 2, 0)
                u.set(S.H1, floor(S.RV / 256))               # dividend high
                u.set(S.H2, S.RV % 256)                      # dividend low
            with t.else_() as u:
                u.call(P.reg_get, S.OPSZ, 2)
                u.set(S.H1, S.RV)
                u.call(P.reg_get, S.OPSZ, 0)
                u.set(S.H2, S.RV)
            with t.if_(eq(S.H3, 0)) as u:
                u.call(P.cpu_fault, 0)
                u.stop_script()
            with t.if_(eq(S.REG, 6)) as u:                   # DIV
                with u.if_(ge(S.H1, S.H3)) as w:
                    w.call(P.cpu_fault, 0)
                    w.stop_script()
                u.call(P.udivp, S.H1, S.H2, S.H3)
            with t.else_() as u:                             # IDIV: divide magnitudes
                u.set(S.X5, floor(S.H1 / S.OP_SGN))              # dividend negative
                u.set(S.X6, floor(S.H3 / S.OP_SGN))              # divisor negative
                with u.if_(eq(S.X5, 1)) as w:                # negate the 2N-bit dividend
                    w.set(S.X7, S.H2)
                    w.set(S.H2, (S.OP_M - S.H2) % S.OP_M)
                    w.set(S.H1, (S.OP_M - 1 - S.H1 + 1 - mathop("ceiling", S.X7 / S.OP_M)) % S.OP_M)
                with u.if_(eq(S.X6, 1)) as w:
                    w.set(S.H3, S.OP_M - S.H3)
                with u.if_(ge(S.H1, S.H3)) as w:
                    w.call(P.cpu_fault, 0)
                    w.stop_script()
                u.call(P.udivp, S.H1, S.H2, S.H3)
                u.set(S.X7, abs_(S.X5 - S.X6))               # quotient negative?
                with u.if_(OR(AND(eq(S.X7, 0), ge(S.QQ, S.OP_SGN)), AND(eq(S.X7, 1), S.QQ > S.OP_SGN))) as w:
                    w.call(P.cpu_fault, 0)
                    w.stop_script()
                with u.if_(eq(S.X7, 1)) as w:
                    w.set(S.QQ, (S.OP_M - S.QQ) % S.OP_M)
                with u.if_(eq(S.X5, 1)) as w:
                    w.set(S.RR, (S.OP_M - S.RR) % S.OP_M)
            with t.if_(eq(S.OPSZ, 1)) as u:
                u.call(P.reg_set, 1, 0, S.QQ)
                u.call(P.reg_set, 1, 4, S.RR)
            with t.else_() as u:
                u.call(P.reg_set, S.OPSZ, 0, S.QQ)
                u.call(P.reg_set, S.OPSZ, 2, S.RR)

    # ---------------------------------------------------------- IMUL r,r/m --
    pr = new("h_imul")                # 0F AF, 69, 6B
    with pr.body() as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        with b.if_(eq(S.OPCODE, 0x1AF)) as t:
            t.call(P.reg_get, S.OPSZ, S.REG)
            t.set(S.H4, S.RV)
        with b.else_() as t:
            with t.if_(eq(S.OPCODE, 0x69)) as u:
                u.call(P.fetch_imm, S.OPSZ)
                fchk(u)
                u.set(S.H4, S.IMM)
            with t.else_() as u:
                u.call(P.fetch8)
                fchk(u)
                u.set(S.H4, (S.FB - 256 * floor(S.FB / 128) + S.OP_M) % S.OP_M)
        b.call(P.smul, S.H3, S.H4)
        b.set(S.ALU_R, S.ML)
        szf(b)
        b.set(S.AF, 0)
        b.set(S.CF, 1 - S.MFIT)
        b.set(S.OF, 1 - S.MFIT)
        b.call(P.reg_set, S.OPSZ, S.REG, S.ML)

    # ----------------------------------------------------- shifts / rotates --
    pr = new("shiftp", ["op", "v", "cnt"])
    with pr.body() as (b, op, v, cnt):
        b.set(S.X1, cnt % 32)                       # masked count (286+)
        b.set(S.X2, N())
        b.set(S.ALU_R, v)
        with b.if_(S.X1 > 0) as t:
            with t.if_(OR(eq(op, 4), eq(op, 6))) as u:      # SHL / SAL
                with u.if_(S.X1 < S.X2) as w:
                    w.set(S.ALU_R, (v % P2(S.X2 - S.X1)) * P2(S.X1))
                    w.set(S.CF, floor(v / P2(S.X2 - S.X1)) % 2)
                with u.else_() as w:
                    w.set(S.ALU_R, 0)
                    w.set(S.CF, 0)
                    with w.if_(AND(eq(S.X1, S.X2), eq(v % 2, 1))) as x:
                        x.set(S.CF, 1)
                u.set(S.OF, abs_(floor(S.ALU_R / S.OP_SGN) - S.CF))
                szf(u)
            with t.if_(eq(op, 5)) as u:                     # SHR
                with u.if_(S.X1 < S.X2) as w:
                    w.set(S.ALU_R, floor(v / P2(S.X1)))
                with u.else_() as w:
                    w.set(S.ALU_R, 0)
                with u.if_(S.X1 > S.X2) as w:
                    w.set(S.CF, 0)
                with u.else_() as w:
                    w.set(S.CF, floor(v / P2(S.X1 - 1)) % 2)
                u.set(S.OF, floor(v / S.OP_SGN))
                szf(u)
            with t.if_(eq(op, 7)) as u:                     # SAR
                u.set(S.X3, v - S.OP_M * floor(v / S.OP_SGN))
                u.set(S.ALU_R, (floor(S.X3 / P2(S.X1)) + S.OP_M) % S.OP_M)
                u.set(S.CF, floor(S.X3 / P2(S.X1 - 1)) % 2)
                u.set(S.OF, 0)
                szf(u)
            with t.if_(eq(op, 0)) as u:                     # ROL
                u.set(S.X3, S.X1 % S.X2)
                u.set(S.ALU_R, (v % P2(S.X2 - S.X3)) * P2(S.X3) + floor(v / P2(S.X2 - S.X3)))
                u.set(S.CF, S.ALU_R % 2)
                u.set(S.OF, abs_(floor(S.ALU_R / S.OP_SGN) - S.CF))
            with t.if_(eq(op, 1)) as u:                     # ROR
                u.set(S.X3, S.X1 % S.X2)
                u.set(S.ALU_R, floor(v / P2(S.X3)) + (v % P2(S.X3)) * P2(S.X2 - S.X3))
                u.set(S.CF, floor(S.ALU_R / S.OP_SGN))
                u.set(S.OF, abs_(floor(S.ALU_R / S.OP_SGN) - floor(S.ALU_R / (S.OP_SGN / 2)) % 2))
            with t.if_(OR(eq(op, 2), eq(op, 3))) as u:      # RCL / RCR: rotate N+1 bits
                u.set(S.X4, S.X2 + 1)
                u.set(S.X3, S.X1 % S.X4)
                with u.if_(eq(op, 3)) as w:                 # RCR = rotate left by (N+1-c)
                    w.set(S.X3, (S.X4 - S.X3) % S.X4)
                u.set(S.X5, v + S.CF * S.OP_M)
                u.set(S.X6, (S.X5 % P2(S.X4 - S.X3)) * P2(S.X3) + floor(S.X5 / P2(S.X4 - S.X3)))
                u.set(S.ALU_R, S.X6 % S.OP_M)
                u.set(S.CF, floor(S.X6 / S.OP_M))
                with u.if_(eq(op, 2)) as w:
                    w.set(S.OF, abs_(floor(S.ALU_R / S.OP_SGN) - S.CF))
                with u.else_() as w:
                    w.set(S.OF, abs_(floor(S.ALU_R / S.OP_SGN) - floor(S.ALU_R / (S.OP_SGN / 2)) % 2))

    pr = new("h_shift")               # C0 C1 D0 D1 D2 D3
    with pr.body() as (b,):
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        with b.if_(S.OPCODE < 0xD0) as t:           # imm8 count (after displacement)
            t.call(P.fetch8)
            fchk(t)
            t.set(S.H4, S.FB)
        with b.else_() as t:
            with t.if_(S.OPCODE < 0xD2) as u:
                u.set(S.H4, 1)
            with t.else_() as u:
                u.call(P.reg_get, 1, 1)
                u.set(S.H4, S.RV)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.call(P.shiftp, S.REG, S.RMV, S.H4)
        with b.if_(S.H4 % 32 > 0) as t:
            t.call(P.rm_set, S.OPSZ, S.ALU_R)
            fchk(t)

    pr = new("h_shxd")                # 0F A4 A5 AC AD  SHLD / SHRD
    with pr.body() as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            t.call(P.fetch8)
            fchk(t)
            t.set(S.H4, S.FB)
        with b.else_() as t:
            t.call(P.reg_get, 1, 1)
            t.set(S.H4, S.RV)
        b.set(S.H4, S.H4 % 32)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        with b.if_(S.H4 > 0) as t:
            t.call(P.reg_get, S.OPSZ, S.REG)
            t.set(S.X2, N())
            t.set(S.X1, S.H4)
            with t.if_(S.X1 > S.X2) as u:                  # count > width is undefined: clamp
                u.set(S.X1, S.X2)
            with t.if_(S.OPCODE < 0x1A8) as u:              # SHLD
                u.set(S.ALU_R, (S.H3 % P2(S.X2 - S.X1)) * P2(S.X1) + floor(S.RV / P2(S.X2 - S.X1)))
                u.set(S.CF, floor(S.H3 / P2(S.X2 - S.X1)) % 2)
            with t.else_() as u:                            # SHRD
                u.set(S.ALU_R, floor(S.H3 / P2(S.X1)) + (S.RV % P2(S.X1)) * P2(S.X2 - S.X1))
                u.set(S.CF, floor(S.H3 / P2(S.X1 - 1)) % 2)
            t.set(S.OF, abs_(floor(S.ALU_R / S.OP_SGN) - floor(S.H3 / S.OP_SGN)))
            szf(t)
            t.call(P.rm_set, S.OPSZ, S.ALU_R)
            fchk(t)
