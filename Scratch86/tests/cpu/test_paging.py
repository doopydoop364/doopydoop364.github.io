"""Directed 386 paging tests (expectations from the SDM): translation, #PF error codes and CR2, WP, A/D bits,
page-crossing accesses, user/supervisor protection, TLB behaviour, restartable demand paging."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_pm_int import kernel, ENTER_USER, FAULT, HALTED  # noqa: E402

PD, PT0, PT1 = 0x10000, 0x11000, 0x12000


def paging(pt1=None, wp=False, extra=""):
    """Identity-map the first MiB (user+writable), then map 0x400000.. through PT1 using `pt1` PTE values."""
    lines = [
        f"mov edi, {PT0}", "mov eax, 7", "mov ecx, 256", "1: stosd", "add eax, 4096", "loop 1b",
        f"mov dword ptr [{PD}], {PT0 | 7}",
        f"mov dword ptr [{PD + 4}], {PT1 | 7}",              # PDE 1 covers 0x400000-0x7FFFFF
    ]
    for i, pte in enumerate(pt1 or []):
        lines.append(f"mov dword ptr [{PT1 + 4 * i}], {pte}")
    lines += [extra, f"mov eax, {PD}", "mov cr3, eax", "mov eax, cr0", "or eax, 0x80000000"]
    if wp:
        lines.append("or eax, 0x10000")
    lines += ["mov cr0, eax"]
    return "\n".join(lines)


def test_mapping_translates_reads_and_writes_through_the_page_tables():
    body = paging([0x50007]) + "\nmov dword ptr [0x400004], 0x1234ABCD\nmov ebx, [0x400004]\nmov cl, [0x400007]"
    r = kernel(body, peek=[{"addr": 0x50004, "len": 4}])
    assert r["state"] == HALTED
    assert r["regs"]["EBX"] == 0x1234ABCD and r["regs"]["ECX"] & 0xFF == 0x12
    assert r["mem"][0]["bytes"] == [0xCD, 0xAB, 0x34, 0x12]


def test_not_present_page_raises_pf_with_cr2_and_error_code_and_leaves_state_untouched():
    for access, err in (("mov ebx, [0x401008]", 0), ("mov dword ptr [0x401008], eax", 2)):
        r = kernel(paging([0x50007, 0]) + "\nmov eax, 0x55\nmov esi, 0x66\n" + access + "\nmov esi, 0x77")
        assert r["state"] == FAULT and r["shutdown"] == 1        # no IDT handler: delivery fails -> shutdown
        assert r["fault"]["vec"] == 14 and r["fault"]["err"] == err
        assert r["cr2"] == 0x401008
        assert r["regs"]["ESI"] == 0x66 and r["regs"]["ESP"] == 0x9000


def test_read_only_page_write_with_and_without_cr0_wp():
    body = paging([0x50007, 0x60005]) + "\nmov dword ptr [0x401000], 0x11223344\nmov ebx, [0x401000]"
    r = kernel(body, peek=[{"addr": 0x60000, "len": 4}], profile=4)          # WP=0: supervisor may write read-only pages
    assert r["state"] == HALTED and r["regs"]["EBX"] == 0x11223344
    r = kernel(paging([0x50007, 0x60005], wp=True) + "\nmov dword ptr [0x401000], 1", profile=4)   # WP exists on the 486
    assert r["fault"]["vec"] == 14 and r["fault"]["err"] == 3     # P=1 (protection), W=1, supervisor
    assert r["cr2"] == 0x401000


def test_accessed_and_dirty_bits_are_set_in_pde_and_pte():
    body = paging([0x50007, 0x60007]) + "\nmov eax, [0x400000]\nmov dword ptr [0x401000], 5"
    r = kernel(body, peek=[{"addr": PT1, "len": 8}, {"addr": PD + 4, "len": 1}])
    pte0 = r["mem"][0]["bytes"][0]
    pte1 = r["mem"][0]["bytes"][4]
    assert pte0 & 0x20 and not pte0 & 0x40                         # read: A only
    assert pte1 & 0x20 and pte1 & 0x40                             # write: A and D
    assert r["mem"][1]["bytes"][0] & 0x20                          # PDE accessed


def test_page_crossing_access_uses_both_pages_and_a_fault_writes_nothing():
    r = kernel(paging([0x50007, 0x60007]) + "\nmov dword ptr [0x400FFE], 0xA1B2C3D4\nmov ebx, [0x400FFE]",
               peek=[{"addr": 0x50FFE, "len": 2}, {"addr": 0x60000, "len": 2}])
    assert r["regs"]["EBX"] == 0xA1B2C3D4
    assert r["mem"][0]["bytes"] == [0xD4, 0xC3] and r["mem"][1]["bytes"] == [0xB2, 0xA1]
    r = kernel(paging([0x50007, 0]) + "\nmov dword ptr [0x400FFE], 0xA1B2C3D4", peek=[{"addr": 0x50FFE, "len": 2}])
    assert r["fault"]["vec"] == 14 and r["fault"]["err"] == 2 and r["cr2"] == 0x401000   # CR2 = first byte on the missing page
    assert r["mem"][0]["bytes"] == [0, 0]                           # nothing was written to the present page
    r = kernel(paging([0x50007, 0]) + "\nmov ebx, [0x400FFE]")
    assert r["fault"]["vec"] == 14 and r["fault"]["err"] == 0 and r["cr2"] == 0x401000


def test_user_mode_cannot_touch_supervisor_pages():
    gates = {14: ("h14", 0xE, 0)}
    extra = "h14:\npop edi\nmov esi, cr2\nhlt\n"
    # page 0 of the window is user (7), page 1 supervisor-only (3)
    body = paging([0x50007, 0x60003]) + ENTER_USER
    for access, err in (("mov eax, [0x400000]\nmov ebx, 1\nmov eax, [0x401000]", 5),
                        ("mov dword ptr [0x401000], 1", 7)):
        user = "user:\nmov ax, 0x23\nmov ds, ax\n" + access + "\nhlt\n"
        r = kernel(body, gates=gates, extra=extra, user=user)
        assert r["state"] == HALTED and r["regs"]["EDI"] == err, access
        assert r["regs"]["ESI"] == 0x401000
        assert r["cpl"] == 0
    # user may use user pages, and may write when W=1
    r = kernel(body, gates=gates, extra=extra, user="user:\nmov ax, 0x23\nmov ds, ax\nmov dword ptr [0x400010], 0x77\nmov ebx, [0x400010]\nint 0x20\n")
    assert r["regs"]["ESI"] != 0x401000


def test_user_write_to_read_only_user_page_faults_even_without_wp():
    gates = {14: ("h14", 0xE, 0)}
    extra = "h14:\npop edi\nhlt\n"
    r = kernel(paging([0x50005]) + ENTER_USER, gates=gates, extra=extra, user="user:\nmov ax, 0x23\nmov ds, ax\nmov dword ptr [0x400000], 1\nhlt\n")
    assert r["regs"]["EDI"] == 7                                    # P, W, U


def test_tlb_keeps_stale_translation_until_invlpg_or_cr3_reload():
    body = paging([0x50007]) + """
        mov dword ptr [0x50000], 0xAAAA0001
        mov dword ptr [0x60000], 0xBBBB0002
        mov dword ptr [0x70000], 0xCCCC0003
        mov eax, [0x400000]                       # fills the TLB: maps to 0x50000
        mov dword ptr [0x12000], 0x60007          # remap the PTE without flushing
        mov ebx, [0x400000]                       # stale: still 0x50000
        invlpg [0x400000]
        mov ecx, [0x400000]                       # now 0x60000
        mov dword ptr [0x12000], 0x70007
        mov edx, [0x400000]                       # stale again
        mov esi, cr3
        mov cr3, esi                              # full flush
        mov edi, [0x400000]                       # 0x70000
    """
    r = kernel(body, profile=4)                                     # INVLPG exists from the 486 on
    e = r["regs"]
    assert (e["EAX"], e["EBX"], e["ECX"], e["EDX"], e["EDI"]) == (0xAAAA0001, 0xAAAA0001, 0xBBBB0002, 0xBBBB0002, 0xCCCC0003)


def test_demand_paging_handler_maps_the_page_and_the_instruction_restarts():
    extra = """
        h14:
        add esp, 4                                # drop the error code
        inc edx                                   # fault counter
        mov dword ptr [0x12008], 0x70007          # map PT1[2] -> physical 0x70000
        iretd                                     # retries the faulting instruction
    """
    body = paging([0x50007, 0x60007, 0]) + "\nxor edx, edx\nmov eax, 0x5A\nmov dword ptr [0x402000], 0xAB\nmov ebx, [0x402000]"
    r = kernel(body, gates={14: ("h14", 0xE, 0)}, extra=extra, peek=[{"addr": 0x70000, "len": 1}])
    assert r["state"] == HALTED and r["regs"]["EDX"] == 1
    assert r["regs"]["EBX"] == 0xAB and r["mem"][0]["bytes"] == [0xAB]


def test_cr0_pg_without_pe_and_paging_off_restores_flat_addressing():
    body = paging([0x50007]) + """
        mov dword ptr [0x400000], 0x1357
        mov eax, cr0
        and eax, 0x7FFFFFFF
        mov cr0, eax                              # paging off: linear == physical again
        mov ebx, [0x50000]
    """
    r = kernel(body)
    assert r["regs"]["EBX"] == 0x1357
