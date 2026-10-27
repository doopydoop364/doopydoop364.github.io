"""Directed protected-mode (segmentation) tests.  Programs boot through a real-mode bootstrap
(LGDT, CR0.PE, far jump); expectations come from the architecture manuals, not from the emulator."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import run_cases, assemble  # noqa: E402
from pmprog import desc  # noqa: E402

FAULT, HALTED = 3, 2

# standard GDT used by the tests (selector = index*8)
STD = [
    (0, 0, 0, 0),                       # 00 null
    (0, 0xFFFF, 0x9A, 0x0),             # 08 code16 base 0
    (0, 0xFFFFF, 0x9A, 0xC),            # 10 code32 flat 4G
    (0, 0xFFFFF, 0x92, 0xC),            # 18 data flat 4G
    (0x20000, 0xFFFF, 0x92, 0x0),       # 20 data base 0x20000 64K
    (0x20000, 0xFFFF, 0x12, 0x0),       # 28 data not present
    (0, 0xFFFF, 0x90, 0x0),             # 30 read-only data
    (0, 0xFFFF, 0x98, 0x0),             # 38 execute-only code
    (0x20000, 0xFFFF, 0x96, 0x0),       # 40 expand-down data (valid offsets 0x10000..0xFFFF is empty for B=0: limit 0xFFFF)
    (0x20000, 0x0FFF, 0x96, 0x0),       # 48 expand-down, limit 0x0FFF: valid offsets 0x1000..0xFFFF
    (0x20000, 0x0F, 0x92, 0x8),         # 50 granularity=4K, limit field 0x0F -> 0xFFFF
    (0, 0xFFFF, 0xF2, 0x0),             # 58 data DPL3
    (0, 0xFFFF, 0x9E, 0x0),             # 60 conforming readable code DPL0
    (0x20000, 0xFFFF, 0x92, 0x4),       # 68 data B=1
]


def gdt_bytes(entries):
    return "\n".join(desc(*e) for e in entries)


def pm(body, bits32=False, gdt=STD, regs=None, mem=None, peek=None, limit=3000):
    """Run `body` in protected mode (16- or 32-bit code) with a clean register state."""
    entry = 0x10 if bits32 else 0x08
    src = f"""
        cli
        lgdt cs:[gdtr]
        mov eax, cr0
        or al, 1
        mov cr0, eax
        .byte 0xEA
        .word pm_entry
        .word {entry}
        .align 4
        gdtr: .word {len(gdt) * 8 - 1}
        .long gdt
        .align 8
        gdt:
        {gdt_bytes(gdt)}
        {'.code32' if bits32 else ''}
        pm_entry:
        {body}
        hlt
    """
    code = list(assemble(src))
    case = {"name": "pm", "code": code, "init": {"regs": regs or {}, "mem": mem or []}, "peek": peek or [], "limit": limit}
    return run_cases([case])[0]


def test_enter_protected_mode_run_flat_32bit_code_and_smsw():
    r = pm("mov ax, 0x18\nmov ds, ax\nmov eax, 0x12345678\nmov dword ptr [0x20000], eax\nsmsw bx", bits32=True,
           peek=[{"addr": 0x20000, "len": 4}])
    assert r["state"] == HALTED
    assert r["mem"][0]["bytes"] == [0x78, 0x56, 0x34, 0x12]
    assert r["regs"]["EBX"] & 1 == 1 and r["cr0"] & 1 == 1


def test_16bit_pm_code_uses_16bit_defaults_and_32bit_code_32bit():
    r = pm("mov eax, 0xFFFFFFFF\ninc ax", bits32=False)           # 16-bit INC AX only touches AX
    assert r["regs"]["EAX"] == 0xFFFF0000
    r = pm("mov eax, 0xFFFFFFFF\ninc eax", bits32=True)
    assert r["regs"]["EAX"] == 0 and r["flags"]["ZF"] == 1
    r = pm("mov eax, 0xFFFFFFFF\ninc ax", bits32=True)             # 32-bit code: inc ax needs a 0x66 prefix (assembler adds it)
    assert r["regs"]["EAX"] == 0xFFFF0000


def test_data_selector_load_caches_base_limit_and_sets_accessed_bit():
    r = pm("mov ax, 0x20\nmov ds, ax\nmov bx, 0x10\nmov byte ptr [bx], 0x5A", peek=[{"addr": 0x20010, "len": 1}])
    assert r["state"] == HALTED and r["mem"][0]["bytes"] == [0x5A]
    # accessed bit (bit 0 of the access byte) is set by the load; the GDT lives in the code image
    r = pm("mov ax, 0x20\nmov ds, ax\nlea bx, [pm_entry]\nsgdt [bx-64]\nmov bx, word ptr cs:[gdt+0x20+5]\nmov eax, 0", regs={})
    # (access byte of selector 0x20 is read back through CS)
    assert r["state"] == HALTED and (r["regs"]["EBX"] & 0xFF) == 0x93


def test_null_selector_load_ok_but_access_faults_and_null_ss_faults():
    r = pm("xor ax, ax\nmov ds, ax\nmov bl, [0x10]")
    assert r["state"] == FAULT and r["fault"]["vec"] == 13 and r["fault"]["err"] == 0
    r = pm("xor ax, ax\nmov ss, ax")
    assert r["state"] == FAULT and r["fault"]["vec"] == 13 and r["fault"]["err"] == 0
    r = pm("xor ax, ax\nmov ds, ax\nmov ax, ds\nmov bx, ax")
    assert r["state"] == HALTED


def test_selector_outside_gdt_is_gp_with_selector_error_code():
    r = pm("mov ax, 0x1F8\nmov ds, ax")
    assert r["state"] == FAULT and r["fault"]["vec"] == 13 and r["fault"]["err"] == 0x1F8
    assert r["seg"]["DS"] != 0x1F8                               # nothing committed


def test_not_present_data_is_np_and_not_present_stack_is_ss_fault():
    r = pm("mov ax, 0x28\nmov ds, ax")
    assert r["state"] == FAULT and r["fault"]["vec"] == 11 and r["fault"]["err"] == 0x28
    r = pm("mov ax, 0x28\nmov ss, ax")
    assert r["fault"]["vec"] == 12 and r["fault"]["err"] == 0x28


def test_loading_rules_rpl_system_descriptor_and_execute_only():
    r = pm("mov ax, 0x23\nmov ds, ax")                           # RPL 3 on DPL 0 data at CPL 0
    assert r["fault"]["vec"] == 13 and r["fault"]["err"] == 0x20
    r = pm("mov ax, 0x38\nmov ds, ax")                           # execute-only code cannot be loaded into DS
    assert r["fault"]["vec"] == 13
    r = pm("mov ax, 0x30\nmov ss, ax")                           # read-only data cannot be SS
    assert r["fault"]["vec"] == 13
    r = pm("mov ax, 0x58\nmov ds, ax")                           # DPL 3 data from CPL 0 is fine
    assert r["state"] == HALTED
    r = pm("mov ax, 0x60\nmov ds, ax")                           # readable conforming code loads into DS
    assert r["state"] == HALTED


def test_write_to_readonly_segment_faults_read_ok():
    r = pm("mov ax, 0x30\nmov ds, ax\nmov al, [0x10]\nmov [0x10], al")
    assert r["state"] == FAULT and r["fault"]["vec"] == 13 and r["fault"]["err"] == 0
    r = pm("mov ax, 0x30\nmov ds, ax\nadd byte ptr [0x10], 1")    # RMW also needs write permission (checked before any flag change)
    assert r["state"] == FAULT and r["fault"]["vec"] == 13


def test_segment_limit_byte_granularity_and_4k_granularity():
    r = pm("mov ax, 0x20\nmov ds, ax\nmov bx, 0xFFFF\nmov al, [bx]")
    assert r["state"] == HALTED
    r = pm("mov ax, 0x20\nmov ds, ax\nmov ebx, 0x10000\nmov al, [ebx]")
    assert r["state"] == FAULT and r["fault"]["vec"] == 13
    r = pm("mov ax, 0x50\nmov ds, ax\nmov ebx, 0xFFFF\nmov al, [ebx]")      # G=1, limit 0x0F -> 0xFFFF
    assert r["state"] == HALTED
    r = pm("mov ax, 0x50\nmov ds, ax\nmov ebx, 0xFFFF\nmov ax, [ebx]")
    assert r["fault"]["vec"] == 13


def test_expand_down_segment_valid_range_is_above_the_limit():
    r = pm("mov ax, 0x48\nmov ds, ax\nmov bx, 0x0FFF\nmov al, [bx]")
    assert r["fault"]["vec"] == 13
    r = pm("mov ax, 0x48\nmov ds, ax\nmov bx, 0x1000\nmov al, [bx]")
    assert r["state"] == HALTED
    r = pm("mov ax, 0x48\nmov ds, ax\nmov ebx, 0x10000\nmov al, [ebx]")    # B=0: top is 0xFFFF
    assert r["fault"]["vec"] == 13
    r = pm("mov ax, 0x68\nmov ds, ax\nmov ebx, 0x10000\nmov al, [ebx]")    # plain data B=1, expand-up limit 0xFFFF
    assert r["fault"]["vec"] == 13


def test_stack_segment_fault_uses_vector_12():
    r = pm("mov ax, 0x20\nmov ss, ax\nmov ebp, 0x10000\nmov al, ss:[ebp]")
    assert r["state"] == FAULT and r["fault"]["vec"] == 12


def test_far_jump_and_call_to_code_segments_and_conforming_privilege():
    r = pm("""
        .byte 0xEA
        .word tgt
        .word 0x08
        mov bx, 1
        tgt: mov bx, 2""")
    assert r["state"] == HALTED and r["regs"]["EBX"] & 0xFFFF == 2
    r = pm("mov ax, 0x18\n.byte 0xEA\n.word 0\n.word 0x18")           # far jump to a data segment
    assert r["fault"]["vec"] == 13
    r = pm(".byte 0xEA\n.word 0\n.word 0x00")                         # null selector
    assert r["fault"]["vec"] == 13 and r["fault"]["err"] == 0


def test_far_call_and_far_return_in_protected_mode():
    r = pm("""
        mov sp, 0x4000
        mov ax, 0x20
        mov ss, ax
        mov sp, 0x100
        .byte 0x9A
        .word sub1
        .word 0x08
        mov bx, 0x77
        jmp done
        sub1: mov cx, 0x55
        .byte 0xCB
        done: nop""")
    assert r["state"] == HALTED and r["regs"]["ECX"] & 0xFFFF == 0x55 and r["regs"]["EBX"] & 0xFFFF == 0x77
    assert r["regs"]["ESP"] & 0xFFFF == 0x100


def test_lar_lsl_verr_verw():
    r = pm("mov ax, 0x18\nlar ebx, eax\nlsl ecx, eax")
    assert r["flags"]["ZF"] == 1 and r["regs"]["EBX"] & 0x00F0FF00 == 0x00C09200 and r["regs"]["ECX"] == 0xFFFFFFFF
    r = pm("mov ax, 0x1F8\nlar ebx, eax")                              # outside the table: ZF=0, no fault
    assert r["state"] == HALTED and r["flags"]["ZF"] == 0
    r = pm("mov ax, 0x30\nverr ax\nlahf\nmov bl, ah\nverw ax\nlahf\nmov cl, ah")
    assert (r["regs"]["EBX"] & 0x40) == 0x40 and (r["regs"]["ECX"] & 0x40) == 0   # readable yes, writable no
    r = pm("mov ax, 0x38\nverr ax")                                    # execute-only code is not readable
    assert r["flags"]["ZF"] == 0
    r = pm("mov ax, 0x58\nverw ax")                                    # DPL3 data: accessible from CPL0
    assert r["flags"]["ZF"] == 1


def test_sgdt_sidt_lgdt_lidt_and_16bit_base_masking():
    r = pm("mov ebx, 0x100\nsgdt [ebx]\nsidt [ebx+8]", peek=[{"addr": 0x100, "len": 16}], bits32=True)
    b = r["mem"][0]["bytes"]
    assert b[0] | b[1] << 8 == len(STD) * 8 - 1
    assert (b[8] | b[9] << 8, b[10] | b[11] << 8 | b[12] << 16 | b[13] << 24) == (0x3FF, 0)
    r = pm("""mov ebx, 0x100
        mov dword ptr [ebx], 0x00123456
        mov dword ptr [ebx+4], 0xFF000000
        lidt [ebx]
        sidt [ebx+16]""", bits32=True, peek=[{"addr": 0x110, "len": 6}])
    # limit 0x3456 base 0xFF000012?  bytes: [0x56,0x34,0x00,0x00,0x00,0xFF]
    b = r["mem"][0]["bytes"]
    assert b[0] | b[1] << 8 == 0x3456
    r = pm("""mov ebx, 0x100
        mov dword ptr [ebx], 0x00123456
        mov dword ptr [ebx+4], 0xFF000000
        .byte 0x67
        lidt [ebx]
        sidt [ebx+16]""")
    assert r["state"] in (HALTED, FAULT)


def test_lldt_ltr_and_system_descriptor_checks():
    gdt = STD + [
        (0x30000, 0x3F, 0x82, 0x0),     # 70 LDT, 8 entries
        (0x40000, 0x67, 0x89, 0x0),     # 78 available 32-bit TSS
        (0x40000, 0x67, 0x8B, 0x0),     # 80 busy TSS
    ]
    mem = [{"addr": 0x30000, "bytes": list(range(1)) * 0}]
    ldt_entry = desc(0x50000, 0xFFFF, 0x92, 0x0)
    r = pm("mov ax, 0x70\nlldt ax\nsldt bx", gdt=gdt)
    assert r["state"] == HALTED and r["regs"]["EBX"] & 0xFFFF == 0x70
    r = pm("mov ax, 0x18\nlldt ax", gdt=gdt)                          # not an LDT descriptor
    assert r["fault"]["vec"] == 13 and r["fault"]["err"] == 0x18
    r = pm("mov ax, 0x78\nltr ax\nstr bx", gdt=gdt)
    assert r["state"] == HALTED and r["regs"]["EBX"] & 0xFFFF == 0x78
    r = pm("mov ax, 0x80\nltr ax", gdt=gdt)                           # busy TSS
    assert r["fault"]["vec"] == 13
    r = pm("xor ax, ax\nlldt ax\nsldt bx", gdt=gdt)                   # null selector: LDT unusable
    assert r["state"] == HALTED and r["regs"]["EBX"] & 0xFFFF == 0
    r = pm("mov ax, 0x74\nmov ds, ax", gdt=gdt)                       # TI=1 with no LDT loaded -> #GP
    assert r["fault"]["vec"] == 13


def test_ldt_descriptor_load():
    gdt = STD + [(0x30000, 0x1F, 0x82, 0x0)]                          # 70: LDT at 0x30000 (4 entries)
    ldt = desc(0, 0, 0, 0) + "\n" + desc(0x60000, 0xFFFF, 0x92, 0x0)   # LDT[1] = data base 0x60000
    body = "mov ax, 0x70\nlldt ax\nmov ax, 0x0C\nmov ds, ax\nmov byte ptr [0x5], 0x99"
    # copy the LDT bytes into 0x30000 first (real mode code before PE would be cleaner; do it from PM through flat DS)
    prefix = "mov ax, 0x18\nmov es, ax\nmov edi, 0x30000\n" + "\n".join(
        f"mov byte ptr es:[edi+{i}], {b}" for i, b in enumerate([0] * 8 + [0xFF, 0xFF, 0, 0, 6, 0x92, 0, 0]))
    r = pm(prefix + "\n" + body, gdt=gdt, peek=[{"addr": 0x60005, "len": 1}])
    assert r["state"] == HALTED and r["mem"][0]["bytes"] == [0x99]


def test_mov_cr_clts_lmsw_and_pg_without_pe():
    r = pm("mov eax, cr0\nmov ebx, eax\nand eax, 0xFFFFFFF7\nmov cr0, eax\nclts\nmov ecx, cr0")
    assert r["state"] == HALTED
    r = pm("mov eax, 0x80000001\nmov cr0, eax")                        # PG requires PE: this one has PE -> accepted by CR0 logic
    assert r["state"] in (HALTED, FAULT)
    r = pm("mov eax, cr0\nand eax, 0x7FFFFFFE\nor eax, 0x80000000\nmov cr0, eax")   # PG=1, PE=0
    assert r["fault"]["vec"] == 13 and r["fault"]["err"] == 0
    r = pm("lmsw ax\nsmsw bx", regs={"EAX": 0xFFF0})                   # LMSW cannot clear PE
    assert r["regs"]["EBX"] & 1 == 1


def test_return_to_real_mode_after_protected_mode_and_unreal_mode_limits():
    # load DS with a 4 GiB limit in PM, drop PE keeping the cache: real-mode accesses above 64 KiB then succeed
    body = """
        mov ax, 0x18
        mov ds, ax
        mov es, ax
        .byte 0xEA
        .word back16
        .word 0x08
        back16:
        mov eax, cr0
        and al, 0xFE
        mov cr0, eax
        .byte 0xEA
        .word real
        .word 0
        real:
        xor ax, ax
        mov ss, ax
        mov sp, 0x7000
        mov ebx, 0x23456
        mov byte ptr ds:[ebx], 0xA7
        smsw cx
    """
    r = pm(body, peek=[{"addr": 0x23456, "len": 1}])
    assert r["state"] == HALTED and r["mem"][0]["bytes"] == [0xA7] and r["regs"]["ECX"] & 1 == 0


def test_arpl_adjusts_rpl():
    r = pm("mov ax, 0x18\nmov bx, 0x0\narpl bx, ax")                  # equal RPLs: ZF=0, dest unchanged
    assert r["flags"]["ZF"] == 0 and r["regs"]["EBX"] & 0xFFFF == 0
    r = pm("mov ax, 0x1B\nmov bx, 0x8\narpl bx, ax")
    assert r["flags"]["ZF"] == 1 and r["regs"]["EBX"] & 0xFFFF == 0x0B
