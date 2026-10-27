"""Opcode handlers (generator code emitting Scratch procedures).

Each handler is a procedure without parameters that reads the shared decode
state (OPCODE, OSZ, ASZ, ...).  Handlers use only the H1..H4 temporaries for
values that must survive helper calls; TA/TB/TC/TD belong to the helpers.

Implemented opcode map (v0.1), see docs/cpu.md for the authoritative table:

  00-3D  ADD OR ADC SBB AND SUB XOR CMP   (all six operand forms each)
  40-4F  INC/DEC r16/r32          50-5F  PUSH/POP r16/r32
  68,6A  PUSH imm                 70-7F  Jcc rel8
  80,81,83  group 1 (ALU r/m,imm) 84-85  TEST        86-87  XCHG r/m,r
  88-8B  MOV                      8C,8E  MOV Sreg    8D     LEA      8F  POP r/m
  90-97  NOP / XCHG eAX,r         98-99  CBW/CWDE, CWD/CDQ
  9C-9F  PUSHF POPF SAHF LAHF     A0-A3  MOV moffs   A8-A9  TEST imm
  B0-BF  MOV r,imm                C2,C3  RET         C6,C7  MOV r/m,imm
  E0-E3  LOOPNE LOOPE LOOP JCXZ   E8,E9,EB  CALL/JMP rel
  F4     HLT                      F5,F8-FD  CMC CLC STC CLI STI CLD STD
  F6,F7  group 3 (TEST NOT NEG)   FE     group 4 (INC DEC r/m8)
  FF     group 5 (INC DEC CALL JMP PUSH)
  Prefixes: 26 2E 36 3E 64 65 (segment), 66 (opsize), 67 (addrsize), F0, F2, F3
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def sx8(x):
    return x - 256 * floor(x / 128)


def abs_(x):
    return mathop("abs", x)


def build_handlers(p, S, mem, P, new, set_size):
    H = {}
    from cpu_math import build_math
    from cpu_handlers2 import build_handlers2

    def fchk(b):
        """Return from the handler if a helper raised an exception (see core.cpu_fault)."""
        with b.if_(S.FAULT_PEND > -1) as t:
            t.stop_script()

    def handler(name):
        pr = new(name)
        H[name] = pr
        return pr.body()

    from cpu_pm import build_pm
    build_math(p, S, mem, P, new, set_size, fchk)
    pm_ents = build_pm(p, S, mem, P, new, set_size, fchk)
    extra = build_handlers2(p, S, mem, P, new, set_size, fchk) + pm_ents

    def jump_rel(b, disp):
        b.set(S.EIP, (S.EIP + disp + S.CODE_MASK) % S.CODE_MASK)

    # ------------------------------------------------------------------ ALU --
    with handler("h_alu_family") as (b,):
        b.set(S.H1, floor(S.OPCODE / 8))           # operation 0..7
        b.set(S.H2, S.OPCODE % 8)                  # form 0..5
        with b.if_(S.H2 < 4) as t:
            with t.if_(eq(S.H2 % 2, 0)) as u:
                set_size(u, 1)
            with t.else_() as u:
                set_size(u, S.OSZ)
            t.call(P.modrm_decode)
            with t.if_(S.H2 < 2) as u:             # r/m <- r/m op reg
                u.call(P.rm_get, S.OPSZ)
                fchk(u)
                u.set(S.H3, S.RMV)
                u.call(P.reg_get, S.OPSZ, S.REG)
                u.call(P.alu, S.H1, S.H3, S.RV)
                with u.if_(ne(S.H1, 7)) as w:
                    w.call(P.rm_set, S.OPSZ, S.ALU_R)
                    fchk(w)
            with t.else_() as u:                   # reg <- reg op r/m
                u.call(P.reg_get, S.OPSZ, S.REG)
                u.set(S.H3, S.RV)
                u.call(P.rm_get, S.OPSZ)
                fchk(u)
                u.call(P.alu, S.H1, S.H3, S.RMV)
                with u.if_(ne(S.H1, 7)) as w:
                    w.call(P.reg_set, S.OPSZ, S.REG, S.ALU_R)
        with b.else_() as t:                       # AL/eAX, imm
            with t.if_(eq(S.H2, 4)) as u:
                set_size(u, 1)
            with t.else_() as u:
                set_size(u, S.OSZ)
            t.call(P.fetch_imm, S.OPSZ)
            t.set(S.H3, S.IMM)
            t.call(P.reg_get, S.OPSZ, 0)
            t.call(P.alu, S.H1, S.RV, S.H3)
            with t.if_(ne(S.H1, 7)) as u:
                u.call(P.reg_set, S.OPSZ, 0, S.ALU_R)

    with handler("h_group1") as (b,):               # 80 81 83
        with b.if_(eq(S.OPCODE, 128)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        with b.if_(eq(S.OPCODE, 129)) as t:
            t.call(P.fetch_imm, S.OPSZ)
            t.set(S.H3, S.IMM)
        with b.else_() as t:                       # 80: imm8   83: imm8 sign-extended
            t.call(P.fetch8)
            with t.if_(eq(S.OPCODE, 131)) as u:
                u.set(S.H3, (sx8(S.FB) + S.OP_M) % S.OP_M)
            with t.if_(eq(S.OPCODE, 128)) as u:
                u.set(S.H3, S.FB)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.call(P.alu, S.REG, S.RMV, S.H3)
        with b.if_(ne(S.REG, 7)) as t:
            t.call(P.rm_set, S.OPSZ, S.ALU_R)
            fchk(t)

    with handler("h_test_rm") as (b,):             # 84 85
        with b.if_(eq(S.OPCODE, 132)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        b.call(P.reg_get, S.OPSZ, S.REG)
        b.call(P.alu, 4, S.H3, S.RV)

    with handler("h_test_acc") as (b,):            # A8 A9
        with b.if_(eq(S.OPCODE, 168)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.fetch_imm, S.OPSZ)
        b.set(S.H3, S.IMM)
        b.call(P.reg_get, S.OPSZ, 0)
        b.call(P.alu, 4, S.RV, S.H3)

    with handler("h_incdec_reg") as (b,):          # 40-4F
        set_size(b, S.OSZ)
        b.call(P.reg_get, S.OSZ, S.OPCODE % 8)
        b.call(P.incdec, floor(S.OPCODE / 8) % 2, S.RV)
        b.call(P.reg_set, S.OSZ, S.OPCODE % 8, S.ALU_R)

    with handler("h_group4") as (b,):              # FE
        set_size(b, 1)
        b.call(P.modrm_decode)
        with b.if_(S.REG < 2) as t:
            t.call(P.rm_get, 1)
            fchk(t)
            t.call(P.incdec, S.REG, S.RMV)
            t.call(P.rm_set, 1, S.ALU_R)
            fchk(t)
        with b.else_() as t:
            t.call(P.cpu_fault, 6)

    with handler("h_group5") as (b,):              # FF
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        with b.if_(S.REG < 2) as t:
            t.call(P.rm_get, S.OPSZ)
            fchk(t)
            t.call(P.incdec, S.REG, S.RMV)
            t.call(P.rm_set, S.OPSZ, S.ALU_R)
            fchk(t)
        with b.else_() as t:
            with t.if_(eq(S.REG, 2)) as u:         # CALL near r/m
                u.call(P.rm_get, S.OSZ)
                fchk(u)
                u.set(S.H3, S.RMV)
                u.call(P.push, S.OSZ, S.EIP)
                u.set(S.EIP, S.H3 % S.CODE_MASK)
            with t.else_() as u:
                with u.if_(eq(S.REG, 4)) as w:     # JMP near r/m
                    w.call(P.rm_get, S.OSZ)
                    fchk(w)
                    w.set(S.EIP, S.RMV % S.CODE_MASK)
                with u.else_() as w:
                    with w.if_(eq(S.REG, 6)) as x: # PUSH r/m
                        x.call(P.rm_get, S.OSZ)
                        fchk(x)
                        x.call(P.push, S.OSZ, S.RMV)
                    with w.else_() as x:           # far CALL/JMP (3,5); /7 is #UD
                        with x.if_(OR(eq(S.REG, 3), eq(S.REG, 5))) as y:
                            y.call(P.grp5_far)
                        with x.else_() as y:
                            y.call(P.cpu_fault, 6)

    with handler("h_group3") as (b,):              # F6 F7
        with b.if_(eq(S.OPCODE, 246)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        with b.if_(S.REG < 2) as t:                # TEST r/m, imm
            t.call(P.rm_get, S.OPSZ)
            fchk(t)
            t.set(S.H3, S.RMV)
            t.call(P.fetch_imm, S.OPSZ)
            t.call(P.alu, 4, S.H3, S.IMM)
        with b.else_() as t:
            with t.if_(eq(S.REG, 2)) as u:         # NOT (no flags)
                u.call(P.rm_get, S.OPSZ)
                fchk(u)
                u.call(P.rm_set, S.OPSZ, S.OP_M - 1 - S.RMV)
                fchk(u)
            with t.else_() as u:
                with u.if_(eq(S.REG, 3)) as w:     # NEG = 0 - x
                    w.call(P.rm_get, S.OPSZ)
                    fchk(w)
                    w.call(P.alu, 5, 0, S.RMV)
                    w.call(P.rm_set, S.OPSZ, S.ALU_R)
                    fchk(w)
                with u.else_() as w:               # MUL/IMUL/DIV/IDIV
                    w.call(P.grp3_muldiv)

    # ------------------------------------------------------------------ MOV --
    with handler("h_mov_rm") as (b,):              # 88 89 8A 8B
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        with b.if_(S.OPCODE < 138) as t:           # 88/89: r/m <- reg
            t.call(P.reg_get, S.OPSZ, S.REG)
            t.call(P.rm_set, S.OPSZ, S.RV)
            fchk(t)
        with b.else_() as t:                       # 8A/8B: reg <- r/m
            t.call(P.rm_get, S.OPSZ)
            fchk(t)
            t.call(P.reg_set, S.OPSZ, S.REG, S.RMV)

    with handler("h_mov_sreg") as (b,):            # 8C 8E
        b.call(P.modrm_decode)
        with b.if_(S.REG > 5) as t:
            t.call(P.cpu_fault, 6)
        with b.else_() as t:
            with t.if_(eq(S.OPCODE, 140)) as u:    # MOV r/m, Sreg
                with u.if_(S.EA_REG) as w:
                    w.call(P.rm_set, S.OSZ, S.SEG[S.REG + 1])
                    fchk(w)
                with u.if_(eq(S.EA_REG, 0)) as w:
                    w.call(P.rm_set, 2, S.SEG[S.REG + 1])
                    fchk(w)
            with t.else_() as u:                   # MOV Sreg, r/m
                with u.if_(eq(S.REG, 1)) as w:     # MOV CS is #UD
                    w.call(P.cpu_fault, 6)
                with u.else_() as w:
                    w.call(P.rm_get, 2)
                    fchk(w)
                    w.call(P.seg_load, S.REG, S.RMV)
                    fchk(w)

    with handler("h_lea") as (b,):                 # 8D
        b.call(P.modrm_decode)
        with b.if_(S.EA_REG) as t:
            t.call(P.cpu_fault, 6)
        with b.else_() as t:
            with t.if_(eq(S.OSZ, 2)) as u:
                u.call(P.reg_set, 2, S.REG, S.EA_OFF % 65536)
            with t.if_(eq(S.OSZ, 4)) as u:
                u.call(P.reg_set, 4, S.REG, S.EA_OFF)

    with handler("h_pop_rm") as (b,):              # 8F /0
        b.set(S.H4, S.GPR[5])                      # ESP before the pop (restored if the store faults)
        b.call(P.pop, S.OSZ)
        b.set(S.H3, S.PV)
        b.call(P.modrm_decode)
        with b.if_(S.REG > 0) as t:
            t.call(P.cpu_fault, 6)
        with b.else_() as t:
            t.call(P.rm_set, S.OSZ, S.H3)
            with t.if_(S.FAULT_PEND > -1) as u:
                u.replace(S.GPR, 5, S.H4)

    with handler("h_xchg_rm") as (b,):             # 86 87
        with b.if_(eq(S.OPCODE, 134)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        b.call(P.reg_get, S.OPSZ, S.REG)
        b.set(S.H4, S.RV)
        b.call(P.rm_set, S.OPSZ, S.H4)
        fchk(b)
        b.call(P.reg_set, S.OPSZ, S.REG, S.H3)

    with handler("h_xchg_acc") as (b,):            # 90-97 (90 = NOP)
        with b.if_(ne(S.OPCODE, 144)) as t:
            t.call(P.reg_get, S.OSZ, 0)
            t.set(S.H3, S.RV)
            t.call(P.reg_get, S.OSZ, S.OPCODE % 8)
            t.set(S.H4, S.RV)
            t.call(P.reg_set, S.OSZ, 0, S.H4)
            t.call(P.reg_set, S.OSZ, S.OPCODE % 8, S.H3)

    with handler("h_mov_imm_reg") as (b,):         # B0-BF
        with b.if_(S.OPCODE < 184) as t:
            t.call(P.fetch8)
            t.call(P.reg_set, 1, S.OPCODE % 8, S.FB)
        with b.else_() as t:
            t.call(P.fetch_imm, S.OSZ)
            t.call(P.reg_set, S.OSZ, S.OPCODE % 8, S.IMM)

    with handler("h_mov_rm_imm") as (b,):          # C6 C7
        with b.if_(eq(S.OPCODE, 198)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        with b.if_(S.REG > 0) as t:
            t.call(P.cpu_fault, 6)
        with b.else_() as t:
            t.call(P.fetch_imm, S.OPSZ)
            t.call(P.rm_set, S.OPSZ, S.IMM)
            fchk(t)

    with handler("h_mov_moffs") as (b,):           # A0-A3
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.fetch_imm, S.ASZ)
        b.set(S.EA_OFF, S.IMM)
        b.call(P.ea_seg_select, 3)
        b.set(S.EA_LIN, (S.SEGBASE[S.EA_SEG + 1] + S.EA_OFF) % 4294967296)
        with b.if_(S.OPCODE < 162) as t:
            t.call(P.ea_chk, S.OPSZ, 0)
        with b.else_() as t:
            t.call(P.ea_chk, S.OPSZ, 1)
        fchk(b)
        with b.if_(S.OPCODE < 162) as t:           # A0/A1: acc <- [moffs]
            with t.if_(eq(S.OPSZ, 1)) as u:
                u.call(mem.mem_rb, S.EA_LIN)
            with t.else_() as u:
                with u.if_(eq(S.OPSZ, 2)) as w:
                    w.call(mem.mem_rw, S.EA_LIN)
                with u.else_() as w:
                    w.call(mem.mem_rd, S.EA_LIN)
            t.call(P.reg_set, S.OPSZ, 0, S.MEMV)
        with b.else_() as t:                       # A2/A3: [moffs] <- acc
            t.call(P.reg_get, S.OPSZ, 0)
            with t.if_(eq(S.OPSZ, 1)) as u:
                u.call(mem.mem_wb, S.EA_LIN, S.RV)
            with t.else_() as u:
                with u.if_(eq(S.OPSZ, 2)) as w:
                    w.call(mem.mem_ww, S.EA_LIN, S.RV)
                with u.else_() as w:
                    w.call(mem.mem_wd, S.EA_LIN, S.RV)

    with handler("h_cbw_cwd") as (b,):             # 98 99
        with b.if_(eq(S.OPCODE, 152)) as t:        # CBW / CWDE
            with t.if_(eq(S.OSZ, 2)) as u:
                u.call(P.reg_get, 1, 0)
                u.call(P.reg_set, 2, 0, (sx8(S.RV) + 65536) % 65536)
            with t.else_() as u:
                u.call(P.reg_get, 2, 0)
                u.call(P.reg_set, 4, 0, (S.RV - 65536 * floor(S.RV / 32768) + 4294967296) % 4294967296)
        with b.else_() as t:                       # CWD / CDQ
            with t.if_(eq(S.OSZ, 2)) as u:
                u.call(P.reg_get, 2, 0)
                u.call(P.reg_set, 2, 2, floor(S.RV / 32768) * 65535)
            with t.else_() as u:
                u.call(P.reg_get, 4, 0)
                u.call(P.reg_set, 4, 2, floor(S.RV / 2147483648) * 4294967295)

    # ---------------------------------------------------------------- stack --
    with handler("h_push_reg") as (b,):            # 50-57
        b.call(P.reg_get, S.OSZ, S.OPCODE % 8)
        b.call(P.push, S.OSZ, S.RV)

    with handler("h_pop_reg") as (b,):             # 58-5F
        b.call(P.pop, S.OSZ)
        b.call(P.reg_set, S.OSZ, S.OPCODE % 8, S.PV)

    with handler("h_push_imm") as (b,):            # 68 6A
        with b.if_(eq(S.OPCODE, 104)) as t:
            t.call(P.fetch_imm, S.OSZ)
            t.call(P.push, S.OSZ, S.IMM)
        with b.else_() as t:
            t.call(P.fetch8)
            t.call(P.push, S.OSZ, (sx8(S.FB) + S.SZ_M[S.OSZ]) % S.SZ_M[S.OSZ])

    with handler("h_pushf") as (b,):               # 9C
        b.call(P.flags_pack)
        with b.if_(eq(S.OSZ, 2)) as t:
            t.call(P.push, 2, S.EFL % 65536)
        with b.else_() as t:
            t.call(P.push, 4, S.EFL - floor(S.EFL / 65536) % 4 * 65536)   # RF/VM read as 0

    with handler("h_popf") as (b,):                # 9D
        b.call(P.pop, S.OSZ)
        b.call(P.flags_unpack, S.PV, S.OSZ)

    with handler("h_sahf_lahf") as (b,):
        with b.if_(eq(S.OPCODE, 158)) as t:        # SAHF: AH -> SF ZF AF PF CF
            t.call(P.reg_get, 1, 4)
            t.set(S.CF, S.RV % 2)
            t.set(S.PF, floor(S.RV / 4) % 2)
            t.set(S.AF, floor(S.RV / 16) % 2)
            t.set(S.ZF, floor(S.RV / 64) % 2)
            t.set(S.SF, floor(S.RV / 128) % 2)
        with b.else_() as t:                       # LAHF
            t.call(P.reg_set, 1, 4, S.SF * 128 + S.ZF * 64 + S.AF * 16 + S.PF * 4 + 2 + S.CF)

    with handler("h_flagops") as (b,):             # F5 F8-FD
        with b.if_(AND(OR(eq(S.OPCODE, 0xFA), eq(S.OPCODE, 0xFB)), eq(S.CR0 % 2, 1),
                       S.CPL > floor(S.EFL_OTHER / 4096) % 4)) as t:      # CLI/STI need CPL <= IOPL
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        b.dispatch(S.OPCODE, [
            (0xF5, 0xF5, lambda t: t.set(S.CF, 1 - S.CF)),
            (0xF8, 0xF8, lambda t: t.set(S.CF, 0)),
            (0xF9, 0xF9, lambda t: t.set(S.CF, 1)),
            (0xFA, 0xFA, lambda t: t.set(S.IF, 0)),
            (0xFB, 0xFB, lambda t: t.set(S.IF, 1)),
            (0xFC, 0xFC, lambda t: t.set(S.DF, 0)),
            (0xFD, 0xFD, lambda t: t.set(S.DF, 1)),
        ])

    # ------------------------------------------------------- control flow --
    with handler("h_jcc") as (b,):                 # 70-7F
        b.call(P.fetch8)
        b.set(S.H3, sx8(S.FB))
        b.call(P.cond_eval, S.OPCODE % 16)
        with b.if_(eq(S.COND, 1)) as t:
            jump_rel(t, S.H3)

    with handler("h_jmp_call_rel") as (b,):        # E8 E9 EB
        with b.if_(eq(S.OPCODE, 235)) as t:
            t.call(P.fetch8)
            t.set(S.H3, sx8(S.FB))
        with b.else_() as t:
            t.call(P.fetch_imm, S.OSZ)
            with t.if_(eq(S.OSZ, 2)) as u:
                u.set(S.H3, S.IMM - 65536 * floor(S.IMM / 32768))
            with t.if_(eq(S.OSZ, 4)) as u:
                u.set(S.H3, S.IMM - 4294967296 * floor(S.IMM / 2147483648))
        with b.if_(eq(S.OPCODE, 232)) as t:
            t.call(P.push, S.OSZ, S.EIP)
        jump_rel(b, S.H3)

    with handler("h_ret") as (b,):                 # C2 C3
        with b.if_(eq(S.OPCODE, 194)) as t:
            t.call(P.fetch_imm, 2)
            t.set(S.H3, S.IMM)
        with b.else_() as t:
            t.set(S.H3, 0)
        b.call(P.pop, S.OSZ)
        b.set(S.EIP, S.PV % S.CODE_MASK)
        with b.if_(S.H3 > 0) as t:                 # release imm16 bytes of stack
            with t.if_(S.STK32) as u:
                u.replace(S.GPR, 5, (S.GPR[5] + S.H3) % 4294967296)
            with t.else_() as u:
                u.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + (S.GPR[5] % 65536 + S.H3) % 65536)

    with handler("h_loop") as (b,):                # E0 E1 E2 E3
        b.call(P.fetch8)
        b.set(S.H3, sx8(S.FB))
        # counter is CX or ECX according to the *address* size
        with b.if_(eq(S.OPCODE, 227)) as t:        # JCXZ / JECXZ
            t.call(P.reg_get, S.ASZ, 1)
            with t.if_(eq(S.RV, 0)) as u:
                jump_rel(u, S.H3)
        with b.else_() as t:
            t.call(P.reg_get, S.ASZ, 1)
            t.set(S.H4, (S.RV - 1 + S.SZ_M[S.ASZ]) % S.SZ_M[S.ASZ])
            t.call(P.reg_set, S.ASZ, 1, S.H4)
            # E2 LOOP: cx != 0.  E1 LOOPE: and ZF=1.  E0 LOOPNE: and ZF=0.
            t.set(S.COND, 1)
            with t.if_(eq(S.OPCODE, 225)) as u:
                u.set(S.COND, S.ZF)
            with t.if_(eq(S.OPCODE, 224)) as u:
                u.set(S.COND, 1 - S.ZF)
            with t.if_(AND(ne(S.H4, 0), eq(S.COND, 1))) as u:
                jump_rel(u, S.H3)

    with handler("h_hlt") as (b,):                 # F4
        with b.if_(AND(eq(S.CR0 % 2, 1), ne(S.CPL, 0))) as t:
            t.call(P.cpu_fault_err, 13, 0)
            t.stop_script()
        b.set(S.CPU_STATE, 2)

    with handler("h_unsupported") as (b,):
        b.call(P.cpu_fault, 6)

    # ---------------------------------------------------------- dispatch --
    ranges = [
        (0x00, 0x3F, "h_alu_family"),
        (0x40, 0x4F, "h_incdec_reg"),
        (0x50, 0x57, "h_push_reg"),
        (0x58, 0x5F, "h_pop_reg"),
        (0x68, 0x68, "h_push_imm"), (0x6A, 0x6A, "h_push_imm"),
        (0x70, 0x7F, "h_jcc"),
        (0x80, 0x81, "h_group1"), (0x83, 0x83, "h_group1"),
        (0x84, 0x85, "h_test_rm"),
        (0x86, 0x87, "h_xchg_rm"),
        (0x88, 0x8B, "h_mov_rm"),
        (0x8C, 0x8C, "h_mov_sreg"), (0x8E, 0x8E, "h_mov_sreg"),
        (0x8D, 0x8D, "h_lea"),
        (0x8F, 0x8F, "h_pop_rm"),
        (0x90, 0x97, "h_xchg_acc"),
        (0x98, 0x99, "h_cbw_cwd"),
        (0x9C, 0x9C, "h_pushf"), (0x9D, 0x9D, "h_popf"),
        (0x9E, 0x9F, "h_sahf_lahf"),
        (0xA0, 0xA3, "h_mov_moffs"),
        (0xA8, 0xA9, "h_test_acc"),
        (0xB0, 0xBF, "h_mov_imm_reg"),
        (0xC2, 0xC3, "h_ret"),
        (0xC6, 0xC7, "h_mov_rm_imm"),
        (0xE0, 0xE3, "h_loop"),
        (0xE8, 0xE9, "h_jmp_call_rel"), (0xEB, 0xEB, "h_jmp_call_rel"),
        (0xF4, 0xF4, "h_hlt"),
        (0xF5, 0xF5, "h_flagops"), (0xF8, 0xFD, "h_flagops"),
        (0xF6, 0xF7, "h_group3"),
        (0xFE, 0xFE, "h_group4"),
        (0xFF, 0xFF, "h_group5"),
    ]
    # Per-opcode map (existing handlers, then the v0.2 ones override), compressed to ranges.
    table = {}
    for lo, hi, nm in ranges:
        for o in range(lo, hi + 1):
            table[o] = nm
    for base in range(0x00, 0x40, 8):          # carve non-ALU slots of 00-3F; they get explicit handlers
        for o in (base + 6, base + 7):
            table[o] = "h_unsupported"
    for lo, hi, nm in [(a, b, n) for a, b, n in extra if b < 0x100]:
        for o in range(lo, hi + 1):
            table[o] = nm
    table[0x6B] = table[0x69] = "h_imul"
    for o in (0xC0, 0xC1, 0xD0, 0xD1, 0xD2, 0xD3):
        table[o] = "h_shift"
    S.DISPATCH = compress(table)
    two = {}
    for lo, hi, nm in [(a, b, n) for a, b, n in extra if b >= 0x100]:
        for o in range(lo, hi + 1):
            two[o] = nm
    two[0x1AF] = "h_imul"
    for o in (0x1A4, 0x1A5, 0x1AC, 0x1AD):
        two[o] = "h_shxd"
    S.DISPATCH2 = compress(two)
    with new("h_0f").body() as (b,):                 # two-byte opcode escape; OPCODE becomes 0x100 + byte
        b.call(P.fetch8)
        b.set(S.OPCODE, S.FB + 256)
        cases = [(lo, hi, (lambda u, nm=nm: u.call(getattr(P, nm)))) for lo, hi, nm in S.DISPATCH2]
        b.dispatch(S.OPCODE, cases, default=lambda u: u.call(P.h_unsupported))
    S.DISPATCH = compress({**table, 0x0F: "h_0f"})
    return H


def compress(table):
    out = []
    for o in sorted(table):
        if out and out[-1][1] == o - 1 and out[-1][2] == table[o]:
            out[-1] = (out[-1][0], o, table[o])
        else:
            out.append((o, o, table[o]))
    return out
