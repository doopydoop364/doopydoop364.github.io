"""v0.1 acceptance tests: genuine x86 machine code executed by the Scratch blocks.

Machine code comes from GNU as (an independent assembler).  Every instruction
is fetched, decoded and executed by the generated Scratch project inside the
stock scratch-vm.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import run_asm, run_cases, assemble  # noqa: E402

HALTED, FAULT = 2, 3


def test_spec_program_ax_bx_30():
    r = run_asm("mov ax, 10\nadd ax, 20\nmov bx, ax\nhlt")
    assert r["regs"]["EAX"] == 30 and r["regs"]["EBX"] == 30
    assert r["state"] == HALTED
    assert r["insns"] == 4
    assert r["eip"] == 0x7C00 + 9            # B80A00 83C014 89C3 F4 = 9 bytes
    # ADD AX,20 -> 30: no carry/overflow/zero/sign, PF=1 (0x1E has 4 bits set)
    assert r["flags"] == {"CF": 0, "PF": 1, "AF": 0, "ZF": 0, "SF": 0, "TF": 0, "IF": 0, "DF": 0, "OF": 0}


def test_instruction_lengths_advance_eip():
    """EIP must advance by exactly the encoded length; lengths come from Capstone (independent decoder)."""
    from capstone import Cs, CS_ARCH_X86, CS_MODE_16
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    prog = ["nop", "mov al, 5", "mov ax, 0x1234", "add ax, 0x1000", "add ax, 5", "mov word ptr [0x200], ax",
            "mov bx, word ptr [0x200]", "mov byte ptr [bx+si+0x10], 7", "mov dword ptr [bx+si+0x1000], 0x12345678",
            "mov eax, 0x11223344", "add eax, dword ptr [esi*4+0x100]", "push ax", "mov ax, es:[bp+di-2]",
            "xchg eax, ebx", "lea si, [bx+di+5]", "push 0x1234", "pushw 5", "cbw", "lahf"]
    for src in prog:
        code = bytes(assemble(src + "\nhlt"))
        ln = next(iter(md.disasm(code, 0))).size
        r = run_cases([{"name": src, "code": list(code), "limit": 1, "budget": 1}])[0]
        assert r["insns"] == 1, src
        assert r["eip"] == 0x7C00 + ln, (src, hex(r["eip"]), ln)


def test_branch_on_computed_flags():
    # computes 5-5 -> ZF=1 so JZ is taken and skips the 'mov cx, 1'; then 3-5 -> JNZ taken over nothing.
    r = run_asm("""
        mov ax, 5
        sub ax, 5
        jz taken
        mov cx, 1
    taken:
        mov dx, 2
        mov ax, 3
        cmp ax, 5
        jnz also
        mov bx, 9
    also:
        hlt""")
    assert r["regs"]["ECX"] == 0 and r["regs"]["EDX"] & 0xFFFF == 2 and r["regs"]["EBX"] == 0
    assert r["state"] == HALTED


def test_loop_counts_sum():
    # sum 1..10 with DEC/JNZ, and again with LOOP
    r = run_asm("""
        xor ax, ax
        mov cx, 10
    top:
        add ax, cx
        dec cx
        jnz top
        mov bx, ax
        xor ax, ax
        mov cx, 10
    t2: add ax, cx
        loop t2
        hlt""")
    assert r["regs"]["EBX"] == 55 and r["regs"]["EAX"] == 55 and r["regs"]["ECX"] == 0


def test_signed_vs_unsigned_branches():
    # 0xFFFF vs 1: unsigned above, signed less
    r = run_asm("""
        mov ax, 0xFFFF
        cmp ax, 1
        mov bx, 0
        ja unsigned_above
        jmp next
    unsigned_above:
        mov bl, 1
    next:
        jl signed_less
        jmp done
    signed_less:
        mov bh, 1
    done:
        hlt""")
    assert r["regs"]["EBX"] == 0x0101


def test_ram_write_readback():
    r = run_asm("""
        mov ax, 0xBEEF
        mov word ptr [0x1000], ax
        mov byte ptr [0x1002], 0x5A
        xor ax, ax
        mov bx, word ptr [0x1000]
        mov cl, byte ptr [0x1002]
        mov dx, 0x1000
        mov si, dx
        mov di, word ptr [si]
        hlt""", peek=[{"addr": 0x1000, "len": 4}])
    assert r["mem"][0]["bytes"] == [0xEF, 0xBE, 0x5A, 0]    # little endian in physical RAM
    assert r["regs"]["EBX"] == 0xBEEF and r["regs"]["ECX"] & 0xFF == 0x5A and r["regs"]["EDI"] == 0xBEEF


def test_segment_addressing_and_memory_diagnostics():
    r = run_asm("""
        mov ax, 0x1234
        mov ds, ax
        mov word ptr [0x10], 0xCAFE
        hlt""", peek=[{"addr": 0x12350, "len": 2}], init={"vars": {"diag: record memory accesses": 1}})
    assert r["mem"][0]["bytes"] == [0xFE, 0xCA]
    assert r["diag"]["lastWrLin"] == 0x12350 and r["diag"]["lastWrPhys"] == 0x12350
    assert r["diag"]["lastW"] == 2 and r["diag"]["lastVal"] == 0xCAFE
    assert r["diag"]["lastRdLin"] == 0       # instruction fetches are not data reads


def test_stack_push_pop_call_ret():
    r = run_asm("""
        mov ax, 0x1111
        push ax
        mov ax, 0x2222
        push ax
        pop bx
        pop cx
        call sub1
        mov dx, 7
        hlt
    sub1:
        mov si, 0x99
        ret""")
    g = r["regs"]
    assert g["EBX"] == 0x2222 and g["ECX"] == 0x1111 and g["ESI"] == 0x99 and g["EDX"] & 0xFFFF == 7
    assert g["ESP"] == 0x7C00


def test_arithmetic_boundaries():
    cases = [
        ("mov ax, 0x7FFF\nadd ax, 1", dict(OF=1, SF=1, CF=0, ZF=0)),
        ("mov ax, 0xFFFF\nadd ax, 1", dict(OF=0, SF=0, CF=1, ZF=1, PF=1, AF=1)),
        ("mov ax, 0\nsub ax, 1", dict(CF=1, SF=1, OF=0, AF=1)),
        ("mov ax, 0x8000\nsub ax, 1", dict(OF=1, CF=0, SF=0)),
        ("mov al, 0x80\nadd al, 0x80", dict(OF=1, CF=1, ZF=1)),
        ("mov eax, 0xFFFFFFFF\nadd eax, 1", dict(CF=1, ZF=1)),
        ("mov eax, 0x7FFFFFFF\ninc eax", dict(OF=1, SF=1)),
        ("stc\nmov ax, 0xFFFF\ninc ax", dict(CF=1, ZF=1, OF=0)),   # INC preserves CF
        ("mov ax, 0x8000\nneg ax", dict(OF=1, CF=1)),
    ]
    for src, expect in cases:
        r = run_asm(src + "\nhlt")
        for f, v in expect.items():
            assert r["flags"][f] == v, (src, f, r["flags"])


def test_register_widths_and_high_low_bytes():
    r = run_asm("""
        mov eax, 0x12345678
        mov ah, 0xAA
        mov al, 0xBB
        mov cx, 0xCDEF
        mov ebx, 0xFFFFFFFF
        mov bx, 0
        mov dh, 0x11
        mov dl, 0x22
        hlt""")
    g = r["regs"]
    assert g["EAX"] == 0x1234AABB and g["EBX"] == 0xFFFF0000 and g["ECX"] == 0xCDEF and g["EDX"] & 0xFFFF == 0x1122


def test_unsupported_opcode_faults_visibly():
    r = run_asm("nop\nint 0x21\nhlt")        # INT is a v0.2 feature
    assert r["state"] == FAULT
    assert r["fault"] == {"vec": 6, "opc": 0xCD, "eip": 0x7C01}
    assert r["eip"] == 0x7C01                 # faulting instruction address
    assert r["insns"] == 1                    # only the NOP completed


def test_halt_stops_execution():
    r = run_asm("hlt\nmov ax, 1\nhlt")
    assert r["state"] == HALTED and r["regs"]["EAX"] == 0 and r["insns"] == 1


def test_self_modifying_code():
    # overwrite the immediate of a later MOV, then execute it
    r = run_asm("""
        mov byte ptr [0x7C06], 0x42
    target:
        mov al, 0x11
        hlt""")
    assert r["regs"]["EAX"] & 0xFF == 0x42


def test_prefixes_operand_and_segment():
    r = run_asm("""
        mov ax, 0x2000
        mov es, ax
        mov word ptr es:[0x30], 0x7777
        mov eax, 0x11223344
        add eax, 0x01010101
        mov bx, es:[0x30]
        hlt""", peek=[{"addr": 0x20030, "len": 2}])
    assert r["mem"][0]["bytes"] == [0x77, 0x77] and r["regs"]["EAX"] == 0x12233445 and r["regs"]["EBX"] & 0xFFFF == 0x7777
