"""Directed v0.2 tests: 64-bit multiply/divide, exceptions + IVT delivery, REP restart,
shift edge cases, real-mode segment limits, TF, far transfers.  Expected values are
computed in Python (independent of the Scratch blocks)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import run_asm  # noqa: E402

FAULT = 3
HALTED = 2
M32 = 0xFFFFFFFF


def regs32(**kw):
    return {"regs": {k.upper(): v for k, v in kw.items()}}


def s32(x):
    return x - (1 << 32) if x & 0x80000000 else x


# ----------------------------------------------------------------- MUL/DIV --
MUL_PAIRS = [(M32, M32), (0x80000000, 2), (0x12345678, 0x9ABCDEF0), (0, M32), (1, M32), (0x10000, 0x10000),
             (0xFFFF, 0xFFFF), (0x7FFFFFFF, 0x7FFFFFFF), (0x80000000, 0x80000000), (M32, 1)]


def test_mul32_extremes():
    for a, b in MUL_PAIRS:
        r = run_asm("mul ebx\nhlt", init=regs32(eax=a, ebx=b))
        p = a * b
        assert (r["regs"]["EAX"], r["regs"]["EDX"]) == (p & M32, p >> 32), (hex(a), hex(b))
        assert r["flags"]["CF"] == r["flags"]["OF"] == (1 if p >> 32 else 0)


def test_imul32_extremes():
    for a, b in MUL_PAIRS:
        r = run_asm("imul ebx\nhlt", init=regs32(eax=a, ebx=b))
        p = s32(a) * s32(b)
        u = p & ((1 << 64) - 1)
        assert (r["regs"]["EAX"], r["regs"]["EDX"]) == (u & M32, u >> 32), (hex(a), hex(b))
        fits = -(1 << 31) <= p < (1 << 31)
        assert r["flags"]["CF"] == r["flags"]["OF"] == (0 if fits else 1)


def test_imul_two_and_three_operand_forms():
    r = run_asm("imul eax, ebx\nhlt", init=regs32(eax=0x10000, ebx=0x10000))
    assert r["regs"]["EAX"] == 0 and r["flags"]["CF"] == 1
    r = run_asm("imul ecx, ebx, -3\nhlt", init=regs32(ebx=7))
    assert r["regs"]["ECX"] == (-21) & M32 and r["flags"]["CF"] == 0
    r = run_asm("imul cx, bx, 0x1234\nhlt", init=regs32(ebx=0x100))
    assert r["regs"]["ECX"] == (0x123400 & 0xFFFF) and r["flags"]["CF"] == 1


def test_div32_extremes():
    cases = [(0xFFFFFFFF_FFFFFFFF, 0xFFFFFFFF), (0x7FFFFFFF_00000000, 0x80000000), (1_00000000, 3),
             (0x12345678_9ABCDEF0, 0xFFFFFFFF), (M32, 1), (0, 5), (0xFFFFFFFE_00000001, 0xFFFFFFFF)]
    for n, d in cases:
        hi, lo = n >> 32, n & M32
        r = run_asm("div ebx\nhlt", init=regs32(eax=lo, edx=hi, ebx=d))
        if hi >= d:
            assert r["state"] == FAULT and r["fault"]["vec"] == 0, (hex(n), hex(d))
        else:
            assert (r["regs"]["EAX"], r["regs"]["EDX"]) == (n // d, n % d), (hex(n), hex(d))


def test_idiv32_extremes_and_overflow():
    def idiv(n, d):  # truncating signed division, Python reference
        q = abs(n) // abs(d)
        q = -q if (n < 0) != (d < 0) else q
        return q, n - q * d
    cases = [(-(1 << 63), 1), (-(1 << 63), 3), (-(1 << 31), -1), (-(1 << 31) - 1, 1), ((1 << 31) - 1, 1), (1 << 31, 1),
             (-7, 2), (7, -2), (-7, -2), (-(1 << 62), -(1 << 31)), (123456789012, -987654), (-1, 0x7FFFFFFF)]
    for n, d in cases:
        u = n & ((1 << 64) - 1)
        r = run_asm("idiv ebx\nhlt", init=regs32(eax=u & M32, edx=u >> 32, ebx=d & M32))
        q, rem = idiv(n, d)
        if not -(1 << 31) <= q < (1 << 31):
            assert r["state"] == FAULT and r["fault"]["vec"] == 0, (n, d)
        else:
            assert (r["regs"]["EAX"], r["regs"]["EDX"]) == (q & M32, rem & M32), (n, d)


def test_div8_div16_and_zero():
    r = run_asm("div bl\nhlt", init=regs32(eax=0x0234, ebx=0x10))
    assert (r["regs"]["EAX"] & 0xFFFF) == ((0x0234 % 16) << 8) | 0x23
    r = run_asm("div bl\nhlt", init=regs32(eax=0x1234, ebx=0x10))      # quotient 0x123 does not fit in AL
    assert r["state"] == FAULT and r["fault"]["vec"] == 0
    r = run_asm("div bx\nhlt", init=regs32(eax=0x5678, edx=0x0123, ebx=0x1000))
    n = 0x01235678
    assert r["regs"]["EAX"] & 0xFFFF == n // 0x1000 and r["regs"]["EDX"] & 0xFFFF == n % 0x1000
    r = run_asm("nop\ndiv bl\nhlt", init=regs32(eax=5, ebx=0))
    assert r["state"] == FAULT and r["fault"]["vec"] == 0 and r["eip"] == 0x7C01
    assert r["regs"]["EAX"] == 5                      # fault leaves state untouched
    r = run_asm("idiv bl\nhlt", init=regs32(eax=0x8000, ebx=0xFF))   # -32768 / -1 overflows
    assert r["state"] == FAULT and r["fault"]["vec"] == 0


# ------------------------------------------------- IVT delivery / INT / IRET --
def ivt(**vecs):
    mem = []
    for k, (seg, off) in vecs.items():
        n = int(k[1:])
        mem.append({"addr": n * 4, "bytes": [off & 255, off >> 8, seg & 255, seg >> 8]})
    return mem


def test_int_delivers_through_ivt_and_iret_returns():
    src = """
        mov ax, 0x1111
        int 0x21
        mov bx, 0x2222
        hlt
    """
    # handler at 0000:0800: sets cx, IRET
    handler = [0xB9, 0x34, 0x12, 0xCF]     # mov cx,0x1234 ; iret
    init = {"regs": {"ESP": 0x7000}, "mem": ivt(v33=(0, 0x800)) + [{"addr": 0x800, "bytes": handler}],
            "flags": {"CF": 1, "ZF": 1}}
    r = run_asm(src, init=init)
    assert r["state"] == HALTED
    assert (r["regs"]["EAX"] & 0xFFFF, r["regs"]["ECX"] & 0xFFFF, r["regs"]["EBX"] & 0xFFFF) == (0x1111, 0x1234, 0x2222)
    assert r["regs"]["ESP"] == 0x7000                  # frame popped
    assert r["flags"]["CF"] == 1 and r["flags"]["ZF"] == 1   # FLAGS restored by IRET


def test_int_frame_contents_and_if_tf_cleared():
    init = {"regs": {"ESP": 0x7000}, "mem": ivt(v33=(0, 0x800)) + [{"addr": 0x800, "bytes": [0xF4]}],
            "flags": {"IF": 1, "CF": 1}, "seg": {"SS": 0}}
    r = run_asm("int 0x21\nhlt", init=init, peek=[{"addr": 0x6FFA, "len": 6}])
    assert r["state"] == HALTED and r["eip"] == 0x801
    b = r["mem"][0]["bytes"]
    ip, cs, fl = b[0] | b[1] << 8, b[2] | b[3] << 8, b[4] | b[5] << 8
    assert (ip, cs) == (0x7C02, 0)
    assert fl & 1 and fl & 0x200 and fl & 2           # CF, IF, reserved bit 1 saved
    assert r["flags"]["IF"] == 0 and r["flags"]["TF"] == 0


def test_divide_error_vectors_with_faulting_ip():
    init = {"regs": {"ESP": 0x7000, "EAX": 9, "EBX": 0}, "mem": ivt(v0=(0, 0x900)) + [{"addr": 0x900, "bytes": [0xF4]}]}
    r = run_asm("nop\ndiv bl\nhlt", init=init, peek=[{"addr": 0x6FFA, "len": 2}])
    assert r["state"] == HALTED and r["eip"] == 0x901
    assert r["mem"][0]["bytes"][:2] == [0x01, 0x7C]    # saved IP = faulting instruction


def test_into_and_int3_and_bound():
    init = {"regs": {"ESP": 0x7000}, "mem": ivt(v4=(0, 0x800), v3=(0, 0x810), v5=(0, 0x820)) +
            [{"addr": 0x800, "bytes": [0xF4]}, {"addr": 0x810, "bytes": [0xF4]}, {"addr": 0x820, "bytes": [0xF4]}]}
    r = run_asm("mov al,0x7f\nadd al,1\ninto\nhlt", init=init)
    assert r["eip"] == 0x801
    r = run_asm("int3\nhlt", init=init)
    assert r["eip"] == 0x811
    init2 = dict(init, mem=init["mem"] + [{"addr": 0x500, "bytes": [10, 0, 20, 0]}])
    r = run_asm("mov ax, 15\nbound ax, [0x500]\nmov bx, 1\nhlt", init=init2)
    assert r["state"] == HALTED and r["regs"]["EBX"] & 0xFFFF == 1       # in range
    r = run_asm("mov ax, 21\nbound ax, [0x500]\nmov bx, 1\nhlt", init=init2)
    assert r["eip"] == 0x821 and r["regs"]["EBX"] & 0xFFFF == 0           # #BR, bx untouched


def test_unset_vector_stops_visibly_but_set_zero_vector_is_a_real_jump():
    r = run_asm("int 0x40\nhlt")
    assert r["state"] == FAULT and r["fault"]["vec"] == 0x40


# ----------------------------------------------------------------- strings --
def test_rep_movsb_is_restartable_one_iteration_per_step():
    init = {"regs": {"ESI": 0x100, "EDI": 0x200, "ECX": 5}, "mem": [{"addr": 0x100, "bytes": [1, 2, 3, 4, 5, 6]}]}
    r = run_asm("cld\nrep movsb\nhlt", init=init, peek=[{"addr": 0x200, "len": 6}])
    assert r["mem"][0]["bytes"] == [1, 2, 3, 4, 5, 0]
    assert r["regs"]["ECX"] & 0xFFFF == 0 and r["regs"]["ESI"] & 0xFFFF == 0x105
    assert r["insns"] == 1 + 5 + 1                   # cld + 5 iterations + hlt


def test_rep_with_zero_count_and_std():
    r = run_asm("rep stosb\nhlt", init={"regs": {"EDI": 0x300, "ECX": 0, "EAX": 0xAA}}, peek=[{"addr": 0x2FE, "len": 4}])
    assert r["mem"][0]["bytes"] == [0, 0, 0, 0]
    r = run_asm("std\nrep stosw\nhlt", init={"regs": {"EDI": 0x304, "ECX": 3, "EAX": 0xBEEF}}, peek=[{"addr": 0x2FC, "len": 10}])
    assert r["mem"][0]["bytes"] == [0, 0, 0, 0, 0xEF, 0xBE, 0xEF, 0xBE, 0xEF, 0xBE]
    assert r["regs"]["EDI"] & 0xFFFF == 0x2FE


def test_repe_cmpsb_stops_at_first_difference_and_repne_scasb():
    init = {"regs": {"ESI": 0x100, "EDI": 0x200, "ECX": 8},
            "mem": [{"addr": 0x100, "bytes": [1, 2, 3, 9, 5]}, {"addr": 0x200, "bytes": [1, 2, 3, 4, 5]}]}
    r = run_asm("cld\nrepe cmpsb\nhlt", init=init)
    assert r["regs"]["ECX"] & 0xFFFF == 4 and r["regs"]["ESI"] & 0xFFFF == 0x104 and r["flags"]["ZF"] == 0
    init = {"regs": {"EDI": 0x200, "ECX": 10, "EAX": 5}, "mem": [{"addr": 0x200, "bytes": [1, 2, 5, 4]}]}
    r = run_asm("cld\nrepne scasb\nhlt", init=init)
    assert r["regs"]["ECX"] & 0xFFFF == 7 and r["regs"]["EDI"] & 0xFFFF == 0x203 and r["flags"]["ZF"] == 1


def test_string_fault_mid_rep_keeps_progress_and_restartable_state():
    # destination runs off the 64 KiB segment: #GP after 2 iterations, registers show progress
    init = {"regs": {"ESI": 0x100, "EDI": 0xFFFF, "ECX": 5}}
    r = run_asm("rep movsw\nhlt", init=init)
    assert r["state"] == FAULT and r["fault"]["vec"] == 13
    assert r["regs"]["ECX"] & 0xFFFF == 5          # nothing was committed by the faulting iteration
    init = {"regs": {"ESI": 0x100, "EDI": 0xFFFC, "ECX": 5}}
    r = run_asm("rep movsw\nhlt", init=init)       # iterations at FFFC, FFFE succeed; the third wraps to 0 and succeeds
    assert r["state"] == HALTED and r["regs"]["ECX"] & 0xFFFF == 0


# ---------------------------------------------------------- shifts/rotates --
def test_shift_count_zero_changes_nothing_and_count_masks_to_5_bits():
    r = run_asm("shl eax, cl\nhlt", init={"regs": {"EAX": 0x80000001, "ECX": 32}, "flags": {"CF": 1, "ZF": 1, "SF": 1, "OF": 1}})
    assert r["regs"]["EAX"] == 0x80000001
    assert (r["flags"]["CF"], r["flags"]["ZF"], r["flags"]["SF"], r["flags"]["OF"]) == (1, 1, 1, 1)
    r = run_asm("shl al, cl\nhlt", init={"regs": {"EAX": 0xFF, "ECX": 9}})
    assert r["regs"]["EAX"] & 0xFF == 0 and r["flags"]["CF"] == 0 and r["flags"]["ZF"] == 1
    r = run_asm("rcl al, 1\nhlt", init={"regs": {"EAX": 0x80}, "flags": {"CF": 1}})
    assert r["regs"]["EAX"] & 0xFF == 1 and r["flags"]["CF"] == 1 and r["flags"]["OF"] == 1   # OF = MSB(res) xor CF
    r = run_asm("rcr ax, cl\nhlt", init={"regs": {"EAX": 0x1234, "ECX": 17}, "flags": {"CF": 1}})
    assert r["regs"]["EAX"] & 0xFFFF == 0x1234          # 17 = full N+1 rotation


def test_sar_shr_shl_values():
    r = run_asm("sar eax, 4\nhlt", init={"regs": {"EAX": 0x80000000}})
    assert r["regs"]["EAX"] == 0xF8000000
    r = run_asm("shr eax, 31\nhlt", init={"regs": {"EAX": 0x80000000}})
    assert r["regs"]["EAX"] == 1 and r["flags"]["CF"] == 0
    r = run_asm("shl eax, 31\nhlt", init={"regs": {"EAX": 0x3}})
    assert r["regs"]["EAX"] == 0x80000000 and r["flags"]["CF"] == 1


# --------------------------------------------------------- segment limits --
def test_limit_word_access_at_ffff_faults_but_byte_does_not():
    r = run_asm("mov al, byte ptr [bx]\nhlt", init={"regs": {"EBX": 0xFFFF}})
    assert r["state"] == HALTED
    r = run_asm("mov ax, word ptr [bx]\nhlt", init={"regs": {"EBX": 0xFFFF}})
    assert r["state"] == FAULT and r["fault"]["vec"] == 13 and r["eip"] == 0x7C00


def test_limit_32bit_address_beyond_64k_is_gp_and_stack_segment_is_ss():
    r = run_asm("mov al, byte ptr [ebx]\nhlt", init={"regs": {"EBX": 0x10000}})
    assert r["state"] == FAULT and r["fault"]["vec"] == 13
    r = run_asm("mov al, byte ptr [ebp+0x10000]\nhlt", init={"regs": {"EBP": 0}})
    assert r["state"] == FAULT and r["fault"]["vec"] == 12


# --------------------------------------------------------------------- TF --
def test_trap_flag_single_step_traps_after_next_instruction():
    init = {"regs": {"ESP": 0x7000}, "mem": ivt(v1=(0, 0x800)) + [{"addr": 0x800, "bytes": [0xF4]}]}
    src = "pushf\npop ax\nor ax, 0x100\npush ax\npopf\nmov bx, 7\nmov cx, 8\nhlt"
    r = run_asm(src, init=init)
    assert r["state"] == HALTED and r["eip"] == 0x801
    assert r["regs"]["EBX"] & 0xFFFF == 7 and r["regs"]["ECX"] & 0xFFFF == 0   # trap after the instruction following POPF


# ---------------------------------------------------------- far transfers --
def test_far_call_ret_and_les_lds():
    code = """
        call 0x0:0x7C20
        mov bx, 0x55
        hlt
    """
    # easier with raw bytes: far call into a routine at 0000:0820, retf back
    init = {"regs": {"ESP": 0x7000}, "mem": [{"addr": 0x820, "bytes": [0xB9, 0x77, 0x00, 0xCB]}]}   # mov cx,0x77; retf
    r = run_asm(".byte 0x9A, 0x20, 0x08, 0x00, 0x00\nmov bx, 0x55\nhlt", init=init)
    assert r["state"] == HALTED and r["regs"]["ECX"] & 0xFFFF == 0x77 and r["regs"]["EBX"] & 0xFFFF == 0x55
    assert r["regs"]["ESP"] == 0x7000
    init = {"regs": {"EBX": 0x100}, "mem": [{"addr": 0x100, "bytes": [0x34, 0x12, 0x00, 0x20]}]}
    r = run_asm("les ax, [bx]\nhlt", init=init)
    assert r["regs"]["EAX"] & 0xFFFF == 0x1234 and r["seg"]["ES"] == 0x2000
    r = run_asm("lds si, [bx]\nhlt", init=init)
    assert r["seg"]["DS"] == 0x2000 and r["regs"]["ESI"] & 0xFFFF == 0x1234


def test_pusha_popa_enter_leave_xlat_bcd():
    r = run_asm("mov esp, 0x7000\nmov ax,1\nmov cx,2\nmov dx,3\nmov bx,4\nmov bp,6\nmov si,7\nmov di,8\npusha\n"
                "xor ax,ax\nxor cx,cx\nxor di,di\npopa\nhlt")
    e = r["regs"]
    assert (e["EAX"] & 0xFFFF, e["ECX"] & 0xFFFF, e["EDI"] & 0xFFFF, e["ESP"] & 0xFFFF) == (1, 2, 8, 0x7000)
    r = run_asm("mov sp,0x7000\nmov bp,0x1234\nenter 8, 0\nleave\nhlt")
    assert r["regs"]["EBP"] & 0xFFFF == 0x1234 and r["regs"]["ESP"] & 0xFFFF == 0x7000
    r = run_asm("mov bx,0x100\nmov al,3\nxlatb\nhlt", init={"mem": [{"addr": 0x100, "bytes": [9, 8, 7, 6]}]})
    assert r["regs"]["EAX"] & 0xFF == 6
    r = run_asm("mov al,0x79\nadd al,0x35\ndaa\nhlt")
    assert r["regs"]["EAX"] & 0xFF == 0x14 and r["flags"]["CF"] == 1
    r = run_asm("mov ax, 0x0105\nmov bl,0x0a\naad\nhlt")
    assert r["regs"]["EAX"] & 0xFFFF == 15


def test_two_byte_opcodes_basic():
    r = run_asm("movzx eax, bl\nmovsx ecx, bl\nhlt", init={"regs": {"EBX": 0x80}})
    assert r["regs"]["EAX"] == 0x80 and r["regs"]["ECX"] == 0xFFFFFF80
    r = run_asm("bswap eax\nsetnz bl\nhlt", init={"regs": {"EAX": 0x11223344}, "flags": {"ZF": 0}})
    assert r["regs"]["EAX"] == 0x44332211 and r["regs"]["EBX"] & 0xFF == 1
    r = run_asm("bsf ax, cx\nhlt", init={"regs": {"ECX": 0x0100}})
    assert r["regs"]["EAX"] & 0xFFFF == 8 and r["flags"]["ZF"] == 0
    r = run_asm("bsr eax, ecx\nhlt", init={"regs": {"ECX": 0x40000000}})
    assert r["regs"]["EAX"] == 30
    r = run_asm("bsf eax, ecx\nhlt", init={"regs": {"ECX": 0, "EAX": 77}})
    assert r["flags"]["ZF"] == 1
    r = run_asm("bts word ptr [bx], 3\nbt word ptr [bx], 3\nhlt", init={"regs": {"EBX": 0x100}}, peek=[{"addr": 0x100, "len": 2}])
    assert r["mem"][0]["bytes"][0] == 8 and r["flags"]["CF"] == 1
    r = run_asm("bt [bx], si\nhlt", init={"regs": {"EBX": 0x100, "ESI": 20}, "mem": [{"addr": 0x102, "bytes": [0x10]}]})
    assert r["flags"]["CF"] == 1                       # bit 20 = word offset 2 bit 4
    r = run_asm("jz L1\nmov bx,1\nL1:\n.byte 0x0f,0x84,0x02,0x00\nmov cx,1\nmov dx,2\nhlt", init={"flags": {"ZF": 0}})
    assert r["regs"]["EDX"] & 0xFFFF == 2 and r["regs"]["ECX"] & 0xFFFF == 1
    r = run_asm("shld eax, ebx, 8\nhlt", init={"regs": {"EAX": 0x12345678, "EBX": 0x9ABCDEF0}})
    assert r["regs"]["EAX"] == 0x3456789A
    r = run_asm("shrd eax, ebx, 8\nhlt", init={"regs": {"EAX": 0x12345678, "EBX": 0x9ABCDEF0}})
    assert r["regs"]["EAX"] == 0xF0123456
    r = run_asm("xadd [bx], al\nhlt", init={"regs": {"EBX": 0x100, "EAX": 5}, "mem": [{"addr": 0x100, "bytes": [10]}]}, peek=[{"addr": 0x100, "len": 1}])
    assert r["mem"][0]["bytes"] == [15] and r["regs"]["EAX"] & 0xFF == 10
    r = run_asm("cmpxchg [bx], cl\nhlt", init={"regs": {"EBX": 0x100, "EAX": 10, "ECX": 99}, "mem": [{"addr": 0x100, "bytes": [10]}]}, peek=[{"addr": 0x100, "len": 1}])
    assert r["mem"][0]["bytes"] == [99] and r["flags"]["ZF"] == 1


def test_io_string_ops_use_floating_bus_until_devices_exist():
    r = run_asm("cld\nrep insb\nhlt", init={"regs": {"EDX": 0x60, "EDI": 0x200, "ECX": 3}}, peek=[{"addr": 0x1FF, "len": 5}])
    assert r["mem"][0]["bytes"] == [0, 0xFF, 0xFF, 0xFF, 0] and r["regs"]["ECX"] & 0xFFFF == 0
    r = run_asm("cld\nrep outsw\nhlt", init={"regs": {"EDX": 0x80, "ESI": 0x100, "ECX": 4}})
    assert r["state"] == HALTED and r["regs"]["ESI"] & 0xFFFF == 0x108
    r = run_asm("in al, 0x60\nin ax, dx\nout 0x61, al\nhlt", init={"regs": {"EDX": 0x70}})
    assert r["regs"]["EAX"] & 0xFFFF == 0xFFFF
