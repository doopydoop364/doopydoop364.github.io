"""v0.2 handlers: string ops, interrupts, far transfers, segment stack ops, BCD,
frame ops, I/O stubs and the 0F two-byte opcode map.  Generator code.

Conventions follow core.py: faulting helpers return early and the handler checks
`fchk` before it modifies architectural state.  For two-byte opcodes OPCODE holds
0x100 + the second byte.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def abs_(x):
    return mathop("abs", x)


def sx8(x):
    return x - 256 * floor(x / 128)


def build_handlers2(p, S, mem, P, new, set_size, fchk):
    ents = []          # (lo, hi, handler name) overriding the one-byte map
    two = []           # 0F xx map, keys are 0x100 + xx

    def handler(name, lo, hi=None):
        pr = new(name)
        ents.append((lo, lo if hi is None else hi, name))
        return pr.body()

    def P2(k):
        return S.POW2[k + 1]

    def jump_rel(b, disp):
        b.set(S.EIP, (S.EIP + disp + S.CODE_MASK) % S.CODE_MASK)

    def szf(b):
        b.set(S.ZF, 1 - mathop("ceiling", S.ALU_R / S.OP_M))
        b.set(S.SF, floor(S.ALU_R / S.OP_SGN))
        b.set(S.PF, S.PAR[S.ALU_R % 256 + 1])

    # ------------------------------------------------------------- helpers --
    pr = new("mrd", ["size", "lin"])
    with pr.body() as (b, size, lin):
        with b.if_(eq(size, 1)) as t:
            t.call(mem.mem_rb, lin)
        with b.else_() as t:
            with t.if_(eq(size, 2)) as u:
                u.call(mem.mem_rw, lin)
            with t.else_() as u:
                u.call(mem.mem_rd, lin)

    pr = new("mwr", ["size", "lin", "val"])
    with pr.body() as (b, size, lin, val):
        with b.if_(eq(size, 1)) as t:
            t.call(mem.mem_wb, lin, val)
        with b.else_() as t:
            with t.if_(eq(size, 2)) as u:
                u.call(mem.mem_ww, lin, val)
            with t.else_() as u:
                u.call(mem.mem_wd, lin, val)

    pr = new("ea_limit", ["size"])        # read access check of EA_SEG:EA_OFF (see core ea_chk)
    with pr.body() as (b, size):
        b.call(P.ea_chk, size, 0)

    pr = new("ea_limit_w", ["size"])      # write access check
    with pr.body() as (b, size):
        b.call(P.ea_chk, size, 1)

    pr = new("read_far_ptr")              # modrm decoded: H3 = offset (OSZ bytes), H4 = selector
    with pr.body() as (b,):
        with b.if_(S.EA_REG) as t:
            t.call(P.cpu_fault, 6)
            t.stop_script()
        b.call(P.ea_limit, S.OSZ + 2)
        fchk(b)
        b.call(P.mrd, S.OSZ, S.EA_LIN)
        b.set(S.H3, S.MEMV)
        b.call(P.mrd, 2, (S.EA_LIN + S.OSZ) % 4294967296)
        b.set(S.H4, S.MEMV)

    pr = new("io_in", ["port", "size"])  # unmapped ports float high; devices hook in here (v0.5)
    with pr.body() as (b, port, size):
        b.set(S.IOV, S.SZ_M[size] - 1)

    pr = new("io_out", ["port", "size", "val"])
    with pr.body() as (b, port, size, val):
        pass

    # ------------------------------------------------------- string ops --
    def str_src(b):
        """Fetch [seg:SI] (size OPSZ) into H2; SI in H3."""
        b.call(P.ea_seg_select, 3)
        b.set(S.EA_OFF, S.H3)
        b.call(P.ea_limit, S.OPSZ)
        fchk(b)
        b.call(P.mrd, S.OPSZ, (S.SEGBASE[S.EA_SEG + 1] + S.H3) % 4294967296)
        b.set(S.H2, S.MEMV)

    def str_dst_limit(b):
        b.set(S.EA_SEG, 0)
        b.set(S.EA_OFF, S.H4)
        b.call(P.ea_limit_w, S.OPSZ)
        fchk(b)

    with handler("h_string", 0xA4, 0xA7) as (b,):          # MOVS CMPS
        string_body(b, S, P, set_size, fchk, str_src, str_dst_limit, szf)
    ents.append((0xAA, 0xAF, "h_string"))
    with handler("h_ins_outs", 0x6C, 0x6F) as (b,):
        ins_outs_body(b, S, P, set_size, fchk)
    # (A8/A9 are TEST; they are carved out below by the merge, which keeps earlier entries)

    # ----------------------------------------------------------- INT etc. --
    with handler("h_int3", 0xCC) as (b,):
        b.call(P.cpu_trap, 3, 1)

    with handler("h_int", 0xCD) as (b,):
        b.call(P.fetch8)
        fchk(b)
        b.call(P.cpu_trap, S.FB, 1)

    with handler("h_into", 0xCE) as (b,):
        with b.if_(eq(S.OF, 1)) as t:
            t.call(P.cpu_trap, 4, 1)

    with handler("h_iret", 0xCF) as (b,):
        with b.if_(eq(S.CR0 % 2, 1)) as t:
            t.call(P.ret_far_pm, 1, 0)
            t.stop_script()
        b.set(S.H4, S.GPR[5])
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.set(S.H1, S.PV)
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.set(S.H2, S.PV % 65536)
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.set(S.H3, S.PV)
        b.call(P.cs_ret_load, S.H2, S.H1)
        with b.if_(S.FAULT_PEND > -1) as t:                 # restore ESP: nothing is committed on a fault
            t.replace(S.GPR, 5, S.H4)
            t.stop_script()
        b.call(P.flags_unpack, S.H3, S.OSZ)

    with handler("h_bound", 0x62) as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        with b.if_(S.EA_REG) as t:
            t.call(P.cpu_fault, 6)
            t.stop_script()
        b.call(P.ea_limit, S.OSZ * 2)
        fchk(b)
        b.call(P.mrd, S.OSZ, S.EA_LIN)
        b.set(S.H3, S.MEMV - S.OP_M * floor(S.MEMV / S.OP_SGN))
        b.call(P.mrd, S.OSZ, (S.EA_LIN + S.OSZ) % 4294967296)
        b.set(S.H4, S.MEMV - S.OP_M * floor(S.MEMV / S.OP_SGN))
        b.call(P.reg_get, S.OSZ, S.REG)
        b.set(S.H2, S.RV - S.OP_M * floor(S.RV / S.OP_SGN))
        with b.if_(OR(S.H2 < S.H3, S.H2 > S.H4)) as t:
            t.call(P.cpu_fault, 5)

    # ---------------------------------------------------- far transfers --
    with handler("h_far_direct", 0x9A) as (b,):             # CALL ptr16:16/32
        b.call(P.fetch_imm, S.OSZ)
        fchk(b)
        b.set(S.H3, S.IMM)
        b.call(P.fetch_imm, 2)
        fchk(b)
        b.set(S.H4, S.IMM)
        b.call(P.far_xfer, S.H4, S.H3, 1)

    with handler("h_jmp_far", 0xEA) as (b,):
        b.call(P.fetch_imm, S.OSZ)
        fchk(b)
        b.set(S.H3, S.IMM)
        b.call(P.fetch_imm, 2)
        fchk(b)
        b.call(P.far_xfer, S.IMM, S.H3, 0)

    pr = new("grp5_far")                 # FF /3 CALL m16:16, FF /5 JMP m16:16 (modrm decoded, OSZ)
    with pr.body() as (b,):
        b.call(P.read_far_ptr)
        fchk(b)
        with b.if_(eq(S.REG, 3)) as t:
            t.call(P.far_xfer, S.H4, S.H3, 1)
        with b.else_() as t:
            t.call(P.far_xfer, S.H4, S.H3, 0)

    with handler("h_retf", 0xCA, 0xCB) as (b,):
        with b.if_(eq(S.OPCODE, 0xCA)) as t:
            t.call(P.fetch_imm, 2)
            fchk(t)
            t.set(S.H2, S.IMM)
        with b.else_() as t:
            t.set(S.H2, 0)
        with b.if_(eq(S.CR0 % 2, 1)) as t:
            t.call(P.ret_far_pm, 0, S.H2)
            t.stop_script()
        b.set(S.X7, S.GPR[5])
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.set(S.H1, S.PV)
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.call(P.cs_ret_load, S.PV % 65536, S.H1)
        with b.if_(S.FAULT_PEND > -1) as t:
            t.replace(S.GPR, 5, S.X7)
            t.stop_script()
        with b.if_(S.H2 > 0) as t:
            with t.if_(S.STK32) as u:
                u.replace(S.GPR, 5, (S.GPR[5] + S.H2) % 4294967296)
            with t.else_() as u:
                u.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + (S.GPR[5] % 65536 + S.H2) % 65536)

    with handler("h_lxs", 0xC4, 0xC5) as (b,):               # LES LDS
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.read_far_ptr)
        fchk(b)
        b.call(P.seg_load, S.OPCODE % 2 * 3, S.H4)
        fchk(b)
        b.call(P.reg_set, S.OSZ, S.REG, S.H3)

    with handler("h_lxs2", 0x1B2, 0x1B2) as (b,):            # LSS
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.read_far_ptr)
        fchk(b)
        b.call(P.seg_load, 2, S.H4)
        fchk(b)
        b.call(P.reg_set, S.OSZ, S.REG, S.H3)

    with handler("h_lfs_lgs", 0x1B4, 0x1B5) as (b,):         # LFS LGS
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.read_far_ptr)
        fchk(b)
        b.call(P.seg_load, S.OPCODE - 0x1B4 + 4, S.H4)
        fchk(b)
        b.call(P.reg_set, S.OSZ, S.REG, S.H3)

    # ------------------------------------------------- segment push / pop --
    for op in (0x06, 0x0E, 0x16, 0x1E):
        ents.append((op, op, "h_push_seg"))
    two += [(0x1A0, 0x1A0, "h_push_seg"), (0x1A8, 0x1A8, "h_push_seg"),
            (0x1A1, 0x1A1, "h_pop_seg"), (0x1A9, 0x1A9, "h_pop_seg")]
    new("h_push_seg")
    pr = P.h_push_seg
    with pr.body() as (b,):
        with b.if_(S.OPCODE < 0x100) as t:
            t.set(S.H1, floor(S.OPCODE / 8) % 4)
        with b.else_() as t:
            with t.if_(eq(S.OPCODE, 0x1A0)) as u:
                u.set(S.H1, 4)
            with t.else_() as u:
                u.set(S.H1, 5)
        b.call(P.push, S.OSZ, S.SEG[S.H1 + 1])

    for op in (0x07, 0x17, 0x1F):
        ents.append((op, op, "h_pop_seg"))
    new("h_pop_seg")
    with P.h_pop_seg.body() as (b,):
        with b.if_(S.OPCODE < 0x100) as t:
            t.set(S.H1, floor(S.OPCODE / 8) % 4)
        with b.else_() as t:
            with t.if_(eq(S.OPCODE, 0x1A1)) as u:
                u.set(S.H1, 4)
            with t.else_() as u:
                u.set(S.H1, 5)
        b.set(S.X7, S.GPR[5])
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.call(P.seg_load, S.H1, S.PV % 65536)
        with b.if_(S.FAULT_PEND > -1) as t:                 # a faulting load must not consume the stack slot
            t.replace(S.GPR, 5, S.X7)

    # ------------------------------------------------------- PUSHA / POPA --
    with handler("h_pusha", 0x60) as (b,):
        b.set(S.H4, S.GPR[5])
        b.set(S.H1, 0)
        with b.while_not(eq(S.H1, 8)) as w:
            with w.if_(eq(S.H1, 4)) as t:
                t.set(S.H2, S.H4)
                with t.if_(eq(S.OSZ, 2)) as u:
                    u.set(S.H2, S.H4 % 65536)
            with w.else_() as t:
                t.call(P.reg_get, S.OSZ, S.H1)
                t.set(S.H2, S.RV)
            w.call(P.push, S.OSZ, S.H2)
            w.change(S.H1, 1)

    with handler("h_popa", 0x61) as (b,):
        b.set(S.H1, 7)
        with b.while_not(eq(S.H1, -1)) as w:
            w.call(P.pop, S.OSZ)
            fchk(w)
            with w.if_(ne(S.H1, 4)) as t:
                t.call(P.reg_set, S.OSZ, S.H1, S.PV)
            w.change(S.H1, -1)

    with handler("h_enter", 0xC8) as (b,):
        b.call(P.fetch_imm, 2)
        fchk(b)
        b.set(S.X7, S.IMM)                              # frame size
        b.call(P.fetch8)
        fchk(b)
        b.set(S.X8, S.FB % 32)                          # nesting level
        b.call(P.reg_get, S.OSZ, 5)
        b.call(P.push, S.OSZ, S.RV)
        b.set(S.H3, S.GPR[5])                           # frame pointer = ESP after the push
        with b.if_(eq(S.STK32, 0)) as t:
            t.set(S.H3, S.GPR[5] % 65536)
        with b.if_(S.X8 > 0) as t:
            t.set(S.H1, 1)
            with t.while_not(eq(S.H1, S.X8)) as w:
                w.call(P.reg_get, S.OSZ, 5)
                w.set(S.H2, (S.RV - S.OSZ + 65536) % 65536)
                w.call(P.reg_set, S.OSZ, 5, S.H2)
                w.call(P.mrd, S.OSZ, (S.SEGBASE[3] + S.H2) % 4294967296)
                w.call(P.push, S.OSZ, S.MEMV)
                w.change(S.H1, 1)
            t.call(P.push, S.OSZ, S.H3)
        b.call(P.reg_set, S.OSZ, 5, S.H3)
        with b.if_(S.STK32) as t:
            t.replace(S.GPR, 5, (S.GPR[5] - S.X7 + 4294967296) % 4294967296)
        with b.else_() as t:
            t.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + (S.GPR[5] % 65536 - S.X7 + 65536) % 65536)

    with handler("h_leave", 0xC9) as (b,):
        with b.if_(S.STK32) as t:
            t.replace(S.GPR, 5, S.GPR[6])
        with b.else_() as t:
            t.replace(S.GPR, 5, S.GPR[5] - S.GPR[5] % 65536 + S.GPR[6] % 65536)
        b.call(P.pop, S.OSZ)
        fchk(b)
        b.call(P.reg_set, S.OSZ, 5, S.PV)

    with handler("h_xlat", 0xD7) as (b,):
        b.call(P.reg_get, S.ASZ, 3)
        b.call(P.reg_get, 1, 0)
        b.set(S.EA_OFF, (S.GPR[4] + S.RV) % S.SZ_M[S.ASZ])
        with b.if_(eq(S.ASZ, 2)) as t:
            t.set(S.EA_OFF, (S.GPR[4] % 65536 + S.RV) % 65536)
        b.call(P.ea_seg_select, 3)
        b.call(P.ea_limit, 1)
        fchk(b)
        b.call(mem.mem_rb, (S.SEGBASE[S.EA_SEG + 1] + S.EA_OFF) % 4294967296)
        b.call(P.reg_set, 1, 0, S.MEMV)

    # -------------------------------------------------------------- I/O --
    with handler("h_in", 0xE4, 0xE5) as (b,):
        b.call(P.fetch8)
        fchk(b)
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            t.set(S.X8, 1)
        with b.else_() as t:
            t.set(S.X8, S.OSZ)
        b.call(P.io_check, S.FB, S.X8)
        fchk(b)
        b.call(P.io_in, S.FB, S.X8)
        b.call(P.reg_set, S.X8, 0, S.IOV)

    with handler("h_out", 0xE6, 0xE7) as (b,):
        b.call(P.fetch8)
        fchk(b)
        b.set(S.H3, S.FB)
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            t.set(S.X8, 1)
        with b.else_() as t:
            t.set(S.X8, S.OSZ)
        b.call(P.io_check, S.H3, S.X8)
        fchk(b)
        b.call(P.reg_get, S.X8, 0)
        b.call(P.io_out, S.H3, S.X8, S.RV)

    with handler("h_in_dx", 0xEC, 0xED) as (b,):
        b.call(P.reg_get, 2, 2)
        b.set(S.H3, S.RV)
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            t.set(S.X8, 1)
        with b.else_() as t:
            t.set(S.X8, S.OSZ)
        b.call(P.io_check, S.H3, S.X8)
        fchk(b)
        b.call(P.io_in, S.H3, S.X8)
        b.call(P.reg_set, S.X8, 0, S.IOV)

    with handler("h_out_dx", 0xEE, 0xEF) as (b,):
        b.call(P.reg_get, 2, 2)
        b.set(S.H3, S.RV)
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            t.set(S.X8, 1)
        with b.else_() as t:
            t.set(S.X8, S.OSZ)
        b.call(P.io_check, S.H3, S.X8)
        fchk(b)
        b.call(P.reg_get, S.X8, 0)
        b.call(P.io_out, S.H3, S.X8, S.RV)

    with handler("h_wait", 0x9B) as (b,):
        pass

    # ------------------------------------------------------ BCD / ASCII --
    with handler("h_daa_das", 0x27) as (b,):
        bcd_adjust(b, S, P, False)
    ents.append((0x2F, 0x2F, "h_daa_das2"))
    with new("h_daa_das2").body() as (b,):
        bcd_adjust(b, S, P, True)

    with handler("h_aaa", 0x37) as (b,):
        ascii_adjust(b, S, P, False)
    with new("h_aas").body() as (b,):
        ascii_adjust(b, S, P, True)
    ents.append((0x3F, 0x3F, "h_aas"))

    with handler("h_aam", 0xD4) as (b,):
        b.call(P.fetch8)
        fchk(b)
        with b.if_(eq(S.FB, 0)) as t:
            t.call(P.cpu_fault, 0)
            t.stop_script()
        b.call(P.reg_get, 1, 0)
        b.call(P.reg_set, 2, 0, floor(S.RV / S.FB) * 256 + S.RV % S.FB)
        b.set(S.ALU_R, S.RV % S.FB)
        set_size(b, 1)
        szf(b)
        b.set(S.CF, 0)
        b.set(S.OF, 0)
        b.set(S.AF, 0)

    with handler("h_aad", 0xD5) as (b,):
        b.call(P.fetch8)
        fchk(b)
        b.call(P.reg_get, 2, 0)
        b.set(S.ALU_R, (S.RV % 256 + floor(S.RV / 256) * S.FB) % 256)
        b.call(P.reg_set, 2, 0, S.ALU_R)
        set_size(b, 1)
        szf(b)
        b.set(S.CF, 0)
        b.set(S.OF, 0)
        b.set(S.AF, 0)

    # --------------------------------------------------------- 0F family --
    def h2(name, lo, hi=None):
        pr = new(name)
        two.append((lo, lo if hi is None else hi, name))
        return pr.body()

    with h2("h_jcc_near", 0x180, 0x18F) as (b,):
        b.call(P.fetch_imm, S.OSZ)
        fchk(b)
        b.set(S.H3, S.IMM - S.SZ_M[S.OSZ] * floor(S.IMM / S.SZ_SGN[S.OSZ]))
        b.call(P.cond_eval, S.OPCODE % 16)
        with b.if_(eq(S.COND, 1)) as t:
            jump_rel(t, S.H3)

    with h2("h_setcc", 0x190, 0x19F) as (b,):
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.cond_eval, S.OPCODE % 16)
        b.call(P.rm_set, 1, S.COND)
        fchk(b)

    with h2("h_movzx", 0x1B6, 0x1B7) as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.rm_get, S.OPCODE % 2 + 1)
        fchk(b)
        b.call(P.reg_set, S.OSZ, S.REG, S.RMV)

    with h2("h_movsx", 0x1BE, 0x1BF) as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.set(S.H1, S.OPCODE % 2 + 1)
        b.call(P.rm_get, S.H1)
        fchk(b)
        b.set(S.H2, S.RMV - S.SZ_M[S.H1] * floor(S.RMV / S.SZ_SGN[S.H1]))
        b.call(P.reg_set, S.OSZ, S.REG, (S.H2 + S.OP_M) % S.OP_M)

    for op in (0x1A3, 0x1AB, 0x1B3, 0x1BB, 0x1BA):
        two.append((op, op, "h_bt"))
    with new("h_bt").body() as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        with b.if_(eq(S.OPCODE, 0x1BA)) as t:           # group 8: imm8 bit offset, op = REG-4
            t.call(P.fetch8)
            fchk(t)
            t.set(S.H2, S.FB % (S.OPSZ * 8))
            t.set(S.H1, S.REG - 4)
            with t.if_(S.H1 < 0) as u:
                u.call(P.cpu_fault, 6)
                u.stop_script()
        with b.else_() as t:
            t.set(S.H1, floor((S.OPCODE - 0x1A3) / 8))  # A3 BT, AB BTS, B3 BTR, BB BTC
            t.call(P.reg_get, S.OSZ, S.REG)
            t.set(S.X7, S.RV - S.OP_M * floor(S.RV / S.OP_SGN))     # signed bit offset
            t.set(S.H2, S.X7 % (S.OPSZ * 8))
            with t.if_(eq(S.EA_REG, 0)) as u:           # memory: address moves by whole operands
                u.set(S.EA_OFF, S.EA_OFF + floor(S.X7 / (S.OPSZ * 8)) * S.OPSZ)
                with u.if_(eq(S.ASZ, 2)) as w:
                    w.set(S.EA_OFF, (S.EA_OFF + 65536) % 65536)
                with u.else_() as w:
                    w.set(S.EA_OFF, (S.EA_OFF + 4294967296) % 4294967296)
                u.set(S.EA_LIN, (S.SEGBASE[S.EA_SEG + 1] + S.EA_OFF) % 4294967296)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.X1, floor(S.RMV / P2(S.H2)) % 2)       # selected bit
        b.set(S.CF, S.X1)
        with b.if_(S.H1 > 0) as t:
            t.set(S.X2, S.RMV)
            with t.if_(eq(S.H1, 1)) as u:                # BTS
                u.set(S.X2, S.RMV + (1 - S.X1) * P2(S.H2))
            with t.if_(eq(S.H1, 2)) as u:                # BTR
                u.set(S.X2, S.RMV - S.X1 * P2(S.H2))
            with t.if_(eq(S.H1, 3)) as u:                # BTC
                u.set(S.X2, S.RMV + (1 - 2 * S.X1) * P2(S.H2))
            t.call(P.rm_set, S.OPSZ, S.X2)
            fchk(t)

    with h2("h_bsf_bsr", 0x1BC, 0x1BD) as (b,):
        set_size(b, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        with b.if_(eq(S.RMV, 0)) as t:
            t.set(S.ZF, 1)
        with b.else_() as t:
            t.set(S.ZF, 0)
            with t.if_(eq(S.OPCODE, 0x1BC)) as u:
                u.set(S.X1, 0)
                with u.while_not(eq(floor(S.RMV / P2(S.X1)) % 2, 1)) as w:
                    w.change(S.X1, 1)
            with t.else_() as u:
                u.set(S.X1, S.OPSZ * 8 - 1)
                with u.while_not(eq(floor(S.RMV / P2(S.X1)) % 2, 1)) as w:
                    w.change(S.X1, -1)
            t.call(P.reg_set, S.OSZ, S.REG, S.X1)

    with h2("h_bswap", 0x1C8, 0x1CF) as (b,):
        b.call(P.reg_get, 4, S.OPCODE % 8)
        b.set(S.X1, S.RV)
        b.call(P.reg_set, 4, S.OPCODE % 8,
               (S.X1 % 256) * 16777216 + (floor(S.X1 / 256) % 256) * 65536 +
               (floor(S.X1 / 65536) % 256) * 256 + floor(S.X1 / 16777216))

    with h2("h_xadd", 0x1C0, 0x1C1) as (b,):
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        b.call(P.reg_get, S.OPSZ, S.REG)
        b.set(S.H4, S.RV)
        b.call(P.alu, 0, S.H3, S.H4)
        b.call(P.reg_set, S.OPSZ, S.REG, S.H3)           # source <- old dest first: with the same
        b.call(P.rm_set, S.OPSZ, S.ALU_R)                # register the sum must win (rm_get already limit-checked)
        fchk(b)

    with h2("h_cmpxchg", 0x1B0, 0x1B1) as (b,):
        with b.if_(eq(S.OPCODE % 2, 0)) as t:
            set_size(t, 1)
        with b.else_() as t:
            set_size(t, S.OSZ)
        b.call(P.modrm_decode)
        fchk(b)
        b.call(P.rm_get, S.OPSZ)
        fchk(b)
        b.set(S.H3, S.RMV)
        b.call(P.reg_get, S.OPSZ, 0)
        b.call(P.alu, 7, S.RV, S.H3)                     # CMP acc, r/m
        with b.if_(eq(S.ZF, 1)) as t:
            t.call(P.reg_get, S.OPSZ, S.REG)
            t.call(P.rm_set, S.OPSZ, S.RV)
            fchk(t)
        with b.else_() as t:
            t.call(P.reg_set, S.OPSZ, 0, S.H3)

    return ents + two


def string_body(b, S, P, set_size, fchk, str_src, str_dst_limit, szf):
    """One iteration of a string instruction (REP handled by restarting the instruction)."""
    with b.if_(eq(S.OPCODE % 2, 0)) as t:
        set_size(t, 1)
    with b.else_() as t:
        set_size(t, S.OSZ)
    with b.if_(ne(S.PFX_REP, 0)) as t:                  # REP with count 0: done
        t.call(P.reg_get, S.ASZ, 1)
        with t.if_(eq(S.RV, 0)) as u:
            u.stop_script()
    b.call(P.reg_get, S.ASZ, 6)
    b.set(S.H3, S.RV)                                   # SI
    b.call(P.reg_get, S.ASZ, 7)
    b.set(S.H4, S.RV)                                   # DI
    k = floor(S.OPCODE / 2) % 8                         # 2 MOVS 3 CMPS 5 STOS 6 LODS 7 SCAS
    with b.if_(eq(k, 2)) as t:                          # MOVS
        str_src(t)
        str_dst_limit(t)
        t.call(P.mwr, S.OPSZ, (S.SEGBASE[1] + S.H4) % 4294967296, S.H2)
    with b.if_(eq(k, 3)) as t:                          # CMPS: [SI] - [DI]
        str_src(t)
        str_dst_limit(t)
        t.call(P.mrd, S.OPSZ, (S.SEGBASE[1] + S.H4) % 4294967296)
        t.call(P.alu, 7, S.H2, S.MEMV)
    with b.if_(eq(k, 5)) as t:                          # STOS
        str_dst_limit(t)
        t.call(P.reg_get, S.OPSZ, 0)
        t.call(P.mwr, S.OPSZ, (S.SEGBASE[1] + S.H4) % 4294967296, S.RV)
    with b.if_(eq(k, 6)) as t:                          # LODS
        str_src(t)
        t.call(P.reg_set, S.OPSZ, 0, S.H2)
    with b.if_(eq(k, 7)) as t:                          # SCAS: acc - [DI]
        str_dst_limit(t)
        t.call(P.mrd, S.OPSZ, (S.SEGBASE[1] + S.H4) % 4294967296)
        t.set(S.H2, S.MEMV)
        t.call(P.reg_get, S.OPSZ, 0)
        t.call(P.alu, 7, S.RV, S.H2)
    # advance index registers
    b.set(S.X1, S.OPSZ - 2 * S.DF * S.OPSZ)            # +size, or -size when DF=1
    with b.if_(OR(eq(k, 2), eq(k, 3), eq(k, 6))) as t:
        t.call(P.reg_set, S.ASZ, 6, (S.H3 + S.X1 + S.SZ_M[S.ASZ]) % S.SZ_M[S.ASZ])
    with b.if_(ne(k, 6)) as t:
        t.call(P.reg_set, S.ASZ, 7, (S.H4 + S.X1 + S.SZ_M[S.ASZ]) % S.SZ_M[S.ASZ])
    rep_tail(b, S, P, OR(eq(k, 3), eq(k, 7)))


def rep_tail(b, S, P, tests_zf):
    """REP bookkeeping after one iteration: decrement the count and, if the instruction should
    repeat, rewind EIP to the instruction start (prefixes included) so every iteration is a
    separate, interruptible, restartable step."""
    with b.if_(ne(S.PFX_REP, 0)) as t:
        t.call(P.reg_get, S.ASZ, 1)
        t.set(S.X2, (S.RV - 1 + S.SZ_M[S.ASZ]) % S.SZ_M[S.ASZ])
        t.call(P.reg_set, S.ASZ, 1, S.X2)
        t.set(S.X3, 0)
        with t.if_(ne(S.X2, 0)) as u:
            u.set(S.X3, 1)
        if tests_zf is not None:
            with t.if_(tests_zf) as u:                  # REPE / REPNE test ZF
                with u.if_(eq(S.PFX_REP, 1)) as w:
                    with w.if_(eq(S.ZF, 0)) as x:
                        x.set(S.X3, 0)
                with u.else_() as w:
                    with w.if_(eq(S.ZF, 1)) as x:
                        x.set(S.X3, 0)
        with t.if_(eq(S.X3, 1)) as u:
            u.set(S.EIP, S.EIP_START)


def ins_outs_body(b, S, P, set_size, fchk):
    """INS (6C/6D): port DX -> ES:DI.  OUTS (6E/6F): DS:SI -> port DX."""
    with b.if_(eq(S.OPCODE % 2, 0)) as t:
        set_size(t, 1)
    with b.else_() as t:
        set_size(t, S.OSZ)
    with b.if_(ne(S.PFX_REP, 0)) as t:
        t.call(P.reg_get, S.ASZ, 1)
        with t.if_(eq(S.RV, 0)) as u:
            u.stop_script()
    b.call(P.reg_get, 2, 2)
    b.set(S.H1, S.RV)                                   # port
    b.call(P.reg_get, S.ASZ, 6)
    b.set(S.H3, S.RV)
    b.call(P.reg_get, S.ASZ, 7)
    b.set(S.H4, S.RV)
    b.call(P.io_check, S.H1, S.OPSZ)
    fchk(b)
    b.set(S.X1, S.OPSZ - 2 * S.DF * S.OPSZ)
    with b.if_(S.OPCODE < 0x6E) as t:                   # INS
        t.set(S.EA_SEG, 0)
        t.set(S.EA_OFF, S.H4)
        t.call(P.ea_limit, S.OPSZ)
        fchk(t)
        t.call(P.io_in, S.H1, S.OPSZ)
        t.call(P.mwr, S.OPSZ, (S.SEGBASE[1] + S.H4) % 4294967296, S.IOV)
        t.call(P.reg_set, S.ASZ, 7, (S.H4 + S.X1 + S.SZ_M[S.ASZ]) % S.SZ_M[S.ASZ])
    with b.else_() as t:                                 # OUTS
        t.call(P.ea_seg_select, 3)
        t.set(S.EA_OFF, S.H3)
        t.call(P.ea_limit, S.OPSZ)
        fchk(t)
        t.call(P.mrd, S.OPSZ, (S.SEGBASE[S.EA_SEG + 1] + S.H3) % 4294967296)
        t.call(P.io_out, S.H1, S.OPSZ, S.MEMV)
        t.call(P.reg_set, S.ASZ, 6, (S.H3 + S.X1 + S.SZ_M[S.ASZ]) % S.SZ_M[S.ASZ])
    rep_tail(b, S, P, None)


def bcd_adjust(b, S, P, is_das):
    """DAA / DAS (Intel SDM pseudo-code)."""
    b.call(P.reg_get, 1, 0)
    b.set(S.X1, S.RV)                                   # old AL
    b.set(S.X2, S.CF)                                   # old CF
    b.set(S.CF, 0)
    with b.if_(OR(S.X1 % 16 > 9, eq(S.AF, 1))) as t:
        if is_das:
            t.set(S.X3, (S.X1 - 6 + 256) % 256)
            with t.if_(OR(eq(S.X2, 1), S.X1 < 6)) as u:
                u.set(S.CF, 1)
        else:
            t.set(S.X3, (S.X1 + 6) % 256)
            with t.if_(OR(eq(S.X2, 1), S.X1 > 249)) as u:
                u.set(S.CF, 1)
        t.set(S.AF, 1)
    with b.else_() as t:
        t.set(S.X3, S.X1)
        t.set(S.AF, 0)
    with b.if_(OR(S.X1 > 153, eq(S.X2, 1))) as t:
        if is_das:
            t.set(S.X3, (S.X3 - 96 + 256) % 256)
        else:
            t.set(S.X3, (S.X3 + 96) % 256)
        t.set(S.CF, 1)
    b.call(P.reg_set, 1, 0, S.X3)
    b.set(S.ZF, 1 - mathop("ceiling", S.X3 / 256))
    b.set(S.SF, floor(S.X3 / 128))
    b.set(S.PF, S.PAR[S.X3 + 1])


def ascii_adjust(b, S, P, is_aas):
    b.call(P.reg_get, 2, 0)
    b.set(S.X1, S.RV)                                   # AX
    with b.if_(OR(S.X1 % 16 > 9, eq(S.AF, 1))) as t:
        if is_aas:
            t.set(S.X1, (S.X1 - 262 + 65536) % 65536)
        else:
            t.set(S.X1, (S.X1 + 262) % 65536)
        t.set(S.AF, 1)
        t.set(S.CF, 1)
    with b.else_() as t:
        t.set(S.AF, 0)
        t.set(S.CF, 0)
    b.call(P.reg_set, 2, 0, S.X1 - S.X1 % 256 + S.X1 % 16)
