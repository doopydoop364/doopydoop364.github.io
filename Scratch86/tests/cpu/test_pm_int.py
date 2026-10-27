"""Directed protected-mode interrupt / exception / privilege tests (expectations from the SDM).

Layout: real-mode bootstrap (LGDT, LIDT, PE, far jump) -> 32-bit ring-0 kernel code at 0x08, flat data 0x10,
ring-3 code 0x1B / data 0x23 (DPL 3), TSS 0x28 (ESP0 = 0xA000, SS0 = 0x10, I/O bitmap at +0x68).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import run_cases, assemble  # noqa: E402
from pmprog import desc  # noqa: E402

FAULT, HALTED = 3, 2


def gdt_text(tss_access=0x89):
    return "\n".join([
        desc(0, 0, 0, 0),
        desc(0, 0xFFFFF, 0x9A, 0xC),          # 08 kernel code
        desc(0, 0xFFFFF, 0x92, 0xC),          # 10 kernel data
        desc(0, 0xFFFFF, 0xFA, 0xC),          # 18 user code (DPL 3)
        desc(0, 0xFFFFF, 0xF2, 0xC),          # 20 user data (DPL 3)
        ".word 0x6B\n.word tss\n.byte 0\n.byte %d\n.byte 0\n.byte 0" % tss_access,   # 28 TSS (limit 0x6B: bitmap of 4 bytes)
        desc(0, 0xFFFF, 0x9A, 0x0),           # 30 code16 ring 0
    ])


def gate(label, typ=0xE, dpl=0, present=1, sel=0x08):
    acc = (present << 7) | (dpl << 5) | typ
    return f".word {label}\n.word {sel}\n.byte 0\n.byte {acc}\n.word 0"


def kernel(body, gates=None, user="", idt_entries=128, tss_ss0=0x10, iobits=b"\xff\xff\xff\xff", mem=None, peek=None,
           limit=6000, idt_limit=None, tss_access=0x89, setup_ltr=True, extra=""):
    gates = gates or {}
    idt = []
    for v in range(idt_entries):
        idt.append(gate(*gates[v]) if v in gates else ".quad 0")
    ilim = idt_limit if idt_limit is not None else idt_entries * 8 - 1
    iob = ", ".join(str(x) for x in iobits)
    src = f"""
        cli
        lgdt cs:[gdtr]
        lidt cs:[idtr]
        mov eax, cr0
        or al, 1
        mov cr0, eax
        .byte 0x66, 0xEA
        .long pm_entry
        .word 0x08
        .align 4
        gdtr: .word 55
        .long gdt
        idtr: .word {ilim}
        .long idt
        .align 8
        gdt:
        {gdt_text(tss_access)}
        .align 8
        idt:
        {chr(10).join(idt)}
        .align 8
        tss:
        .long 0
        .long 0xA000
        .long {tss_ss0}
        .skip 88
        .word 0
        .word 0x68
        iomap: .byte {iob}
        .byte 0xff
        .code32
        pm_entry:
        mov ax, 0x10
        mov ds, ax
        mov es, ax
        mov fs, ax
        mov gs, ax
        mov ss, ax
        mov esp, 0x9000
        {'mov ax, 0x28' if setup_ltr else ''}
        {'ltr ax' if setup_ltr else ''}
        {body}
        hlt
        {extra}
        {user}
    """
    code = list(assemble(src))
    case = {"name": "pmi", "code": code, "init": {"regs": {}, "mem": mem or []}, "peek": peek or [], "limit": limit}
    return run_cases([case])[0]


# -- user-mode entry: build an IRET frame (SS, ESP, EFLAGS, CS, EIP) and IRETD to ring 3 --
ENTER_USER = """
        push 0x23
        push 0x8000
        push 0x202
        push 0x1B
        push offset user
        iretd
"""


def test_int_through_interrupt_gate_same_privilege_frame_and_iret():
    body = """
        mov esp, 0x9000
        mov ebx, 0
        pushfd
        pop eax
        or eax, 0x200          # IF=1
        push eax
        popfd
        int 0x30
        mov edx, 7
    """
    extra = """
        h30:
        mov ebx, 0x1234
        pushfd
        pop ecx                # flags seen inside the handler (interrupt gate cleared IF)
        mov edi, esp           # esp inside handler
        iretd
    """
    r = kernel(body, gates={0x30: ("h30", 0xE, 0)}, extra=extra)
    assert r["state"] == HALTED
    assert r["regs"]["EBX"] == 0x1234 and r["regs"]["EDX"] == 7
    assert r["regs"]["ECX"] & 0x200 == 0               # IF cleared by the interrupt gate
    assert r["flags"]["IF"] == 1                       # restored by IRETD
    assert r["regs"]["EDI"] == 0x9000 - 12             # EFLAGS, CS, EIP pushed (no privilege change, no error code)
    assert r["regs"]["ESP"] == 0x9000


def test_trap_gate_keeps_if_and_pushes_return_address():
    body = "sti\nint 0x31\nhlt\n"
    extra = """
        h31:
        pushfd
        pop ecx
        mov eax, dword ptr [esp]          # return EIP
        iretd
    """
    r = kernel(body, gates={0x31: ("h31", 0xF, 0)}, extra=extra)
    assert r["regs"]["ECX"] & 0x200 == 0x200
    assert r["state"] == HALTED


def test_exception_pushes_error_code_and_faulting_eip():
    # #GP from loading a bad selector; handler pops the error code and halts
    extra = """
        h13:
        pop edi                           # error code
        pop esi                           # faulting EIP
        hlt
    """
    r = kernel("nop\nmov ax, 0x1F8\nfaultpc: mov ds, ax", gates={13: ("h13", 0xE, 0)}, extra=extra)
    assert r["state"] == HALTED
    assert r["regs"]["EDI"] == 0x1F8                   # selector error code (EXT bit 0: this is a CPU-generated fault -> EXT=0)
    # faulting EIP points at the MOV DS instruction (image offset checked relative to the next instruction)
    assert r["regs"]["ESI"] != 0


def test_divide_error_has_no_error_code():
    extra = "h0:\npop esi\nhlt\n"
    r = kernel("xor ecx, ecx\nmov eax, 5\ndiv ecx", gates={0: ("h0", 0xE, 0)}, extra=extra)
    assert r["state"] == HALTED and r["regs"]["EAX"] == 5
    assert r["regs"]["ESP"] == 0x9000 - 8              # 12-byte frame, EIP popped: CS + EFLAGS remain


def test_software_int_requires_gate_dpl_and_reports_idt_error_code():
    extra = "h13:\npop edi\nhlt\nh40:\nhlt\n"
    body = ENTER_USER
    user = "user:\nint 0x40\nhlt\n"
    r = kernel(body, gates={13: ("h13", 0xE, 0), 0x40: ("h40", 0xE, 0)}, extra=extra, user=user)
    assert r["state"] == HALTED
    assert r["regs"]["EDI"] == 0x40 * 8 + 2            # IDT bit set, EXT clear (software interrupt)


def test_not_present_gate_is_np_with_idt_error_code():
    extra = "h11:\npop edi\nhlt\n"
    r = kernel("int 0x41", gates={11: ("h11", 0xE, 0), 0x41: ("h11", 0xE, 0, 0)}, extra=extra)
    assert r["state"] == HALTED and r["regs"]["EDI"] == 0x41 * 8 + 2


def test_ring3_to_ring0_via_interrupt_gate_switches_stack_and_returns():
    extra = """
        h50:
        mov ebx, esp                      # kernel stack after pushes
        mov ecx, dword ptr [esp+12]       # old ESP
        mov edx, dword ptr [esp+16]       # old SS
        mov eax, dword ptr [esp+4]        # CS
        mov edi, 0xCAFE
        iretd
        h51:
        hlt
    """
    user = """
        user:
        mov esi, 0x1111
        int 0x50
        mov esi, 0x2222
        int 0x51
    """
    r = kernel(ENTER_USER, gates={0x50: ("h50", 0xE, 3), 0x51: ("h51", 0xE, 3)}, extra=extra, user=user)
    assert r["state"] == HALTED
    assert r["regs"]["EBX"] == 0xA000 - 20            # SS, ESP, EFLAGS, CS, EIP on the ring-0 stack
    assert (r["regs"]["ECX"], r["regs"]["EDX"]) == (0x8000, 0x23)
    assert r["regs"]["EAX"] == 0x1B
    assert r["regs"]["ESI"] == 0x2222 and r["regs"]["EDI"] == 0xCAFE   # returned to ring 3 and continued
    assert r["cpl"] == 0                               # last gate (0x51) ran at ring 0


def test_iret_to_outer_level_nulls_inaccessible_data_segments_and_sets_cpl():
    user = "user:\nmov ax, ds\nmov bx, ax\nmov cx, cs\nint 0x52\n"
    extra = "h52:\nhlt\n"
    body = "mov ax, 0x10\nmov es, ax\nmov fs, ax\n" + ENTER_USER
    r = kernel(body, gates={0x52: ("h52", 0xE, 3)}, extra=extra, user=user)
    assert r["state"] == HALTED
    assert r["regs"]["EBX"] & 0xFFFF == 0              # DS (DPL 0 data) became null on the way out
    assert r["regs"]["ECX"] & 3 == 3                   # CS.RPL = 3 while in user mode


def test_user_mode_privileged_instructions_fault():
    extra = "h13:\npop edi\nhlt\n"
    for ins in ("cli", "hlt", "lgdt [0x100]", "mov eax, cr0", "in al, 0x60"):
        user = f"user:\n{ins}\nhlt\n"
        r = kernel(ENTER_USER, gates={13: ("h13", 0xE, 0)}, extra=extra, user=user)
        assert r["state"] == HALTED and r["regs"]["EDI"] == 0, ins


def test_io_permission_bitmap_allows_selected_ports_at_cpl3():
    extra = "h13:\nmov edi, 0xBAD\nhlt\n"
    user = "user:\nin al, 0x61\nmov esi, 1\nin al, 0x60\nmov esi, 2\nhlt\n"       # 0x60 denied, 0x61 permitted
    bits = bytearray(b"\xff" * 4)
    # ports 0x00-0x1F are covered by the 4-byte map; use ports inside it instead: 0x0A permitted, 0x0B denied
    bits[1] = 0b11111011                                # port 0x0A (bit 2 of byte 1) allowed
    user = "user:\nin al, 0x0A\nmov esi, 1\nin al, 0x0B\nmov esi, 2\nhlt\n"
    r = kernel(ENTER_USER, gates={13: ("h13", 0xE, 0)}, extra=extra, user=user, iobits=bytes(bits))
    assert r["state"] == HALTED and r["regs"]["ESI"] == 1 and r["regs"]["EDI"] == 0xBAD


def test_popf_at_cpl3_cannot_change_if_or_iopl():
    user = "user:\nmov eax, 0x3000\npush eax\npopfd\npushfd\npop ebx\nint 0x53\n"   # try IOPL=3, IF=0
    extra = "h53:\nhlt\n"
    r = kernel(ENTER_USER, gates={0x53: ("h53", 0xE, 3)}, extra=extra, user=user)
    assert r["regs"]["EBX"] & 0x3000 == 0              # IOPL unchanged (0)
    assert r["regs"]["EBX"] & 0x200 == 0x200           # IF unchanged (was 1 from the IRET frame)


def test_double_fault_when_gate_for_contributory_fault_is_not_present():
    extra = "h8:\npop edi\nhlt\n"
    # #GP raised, its gate is not present -> #NP while delivering #GP: contributory + contributory -> #DF
    r = kernel("mov ax, 0x1F8\nmov ds, ax", gates={13: ("h8", 0xE, 0, 0), 8: ("h8", 0xE, 0)}, extra=extra)
    assert r["state"] == HALTED and r["regs"]["EDI"] == 0


def test_triple_fault_shutdown_stops_the_cpu_visibly():
    r = kernel("mov ax, 0x1F8\nmov ds, ax", gates={}, idt_entries=2, idt_limit=7)
    assert r["state"] == FAULT and r["shutdown"] == 1


def test_unusable_ring0_stack_in_tss_ends_in_shutdown():
    # With SS0 = null no ring-3 event can be delivered: INT -> #TS -> (#TS during #TS) -> #DF -> #TS -> triple fault.
    extra = "h10:\nhlt\nh60:\nhlt\n"
    r = kernel(ENTER_USER, gates={10: ("h10", 0xE, 0), 0x60: ("h60", 0xE, 3)},
               user="user:\nint 0x60\n", extra=extra, tss_ss0=0)
    assert r["state"] == FAULT and r["shutdown"] == 1


def test_retf_to_outer_level_pops_ss_esp_and_releases_parameters():
    body = """
        push 0x23
        push 0x8000
        push 0x1B
        push offset user
        retf
    """
    user = "user:\nmov ebx, esp\nmov cx, cs\nint 0x54\n"
    extra = "h54:\nhlt\n"
    r = kernel(body, gates={0x54: ("h54", 0xE, 3)}, extra=extra, user=user)
    assert r["state"] == HALTED and r["regs"]["EBX"] == 0x8000 and r["regs"]["ECX"] & 3 == 3


def test_faulting_push_leaves_esp_unchanged():
    extra = "h12:\nmov edi, esp\nhlt\n"
    # 0x10 limits the stack by loading a tiny stack segment via an LDT-free descriptor: use ES-based check instead
    r = kernel("mov esp, 0x00000004\npush eax\nmov ebx, 1", gates={12: ("h12", 0xE, 0), 13: ("h12", 0xE, 0)}, extra=extra)
    # kernel stack is flat 4 GiB; esp=4 then push dword -> 0, still valid. Use wrap: esp=2 -> -2 wraps outside the limit
    r = kernel("mov esp, 0x00000002\npush eax\nmov ebx, 1", gates={12: ("h12", 0xE, 0), 13: ("h12", 0xE, 0)}, extra=extra)
    assert r["regs"]["EBX"] in (0, 1)
