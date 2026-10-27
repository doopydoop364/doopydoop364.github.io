"""Wrap generator programs so they run in 32-bit protected mode (differential tests vs Unicorn).

The bootstrap is ordinary real-mode code: LGDT, set CR0.PE, far jump into a 32-bit code segment,
load data selectors whose bases/limits reproduce the real-mode windows (DS=0x10000, ES=0x20000,
SS=0x30000, 64 KiB, 16-bit stack), restore the generator's initial register/flag state, run the
program.  Both engines execute the same bytes; only Scratch86 runs them as Scratch blocks.
"""

CS32, DS_W, ES_W, SS_W, FLAT = 0x08, 0x10, 0x18, 0x20, 0x28


def desc(base, limit, access, flags):
    """8-byte descriptor as .byte list."""
    b = [limit & 255, (limit >> 8) & 255, base & 255, (base >> 8) & 255, (base >> 16) & 255, access,
         ((flags & 15) << 4) | ((limit >> 16) & 15), (base >> 24) & 255]
    return ".byte " + ", ".join(str(x) for x in b)


GDT = [
    desc(0, 0, 0, 0),
    desc(0, 0xFFFF, 0x9A, 0x0),         # 08 code16 (generator programs use 16-bit defaults: CX/SI/DI)
    desc(0x10000, 0xFFFF, 0x92, 0x4),   # 10 data, base 0x10000, 64 KiB (D/B=1 harmless for DS/ES)
    desc(0x20000, 0xFFFF, 0x92, 0x4),   # 18
    desc(0x30000, 0xFFFF, 0x92, 0x0),   # 20 stack, 16-bit (B=0)
    desc(0, 0xFFFFF, 0x92, 0xC),        # 28 flat data
    desc(0, 0xFFFFF, 0x9A, 0xC),        # 30 code32, 4 GiB
]


PAGE_MAP = {0x10: 0x80, 0x20: 0x81, 0x30: 0x82, 0x31: 0x90}      # linear page -> physical page (windows)


def paging_setup():
    """16-bit PM code that builds PD@0x10000 / PT@0x11000 (identity map of the first MiB, windows remapped)
    through the flat FS segment and turns paging on."""
    lines = ["mov ax, 0x28", "mov fs, ax", "mov edi, 0x11000", "mov eax, 3", "mov ecx, 256",
             "pg_loop: mov dword ptr fs:[edi], eax", "add edi, 4", "add eax, 4096", "dec ecx", "jnz pg_loop",
             "mov edi, 0x10000", "mov dword ptr fs:[edi], %d" % (0x11000 | 3)]       # (registers: 16-bit code cannot encode 32-bit absolute offsets)
    for lp, pp in PAGE_MAP.items():
        lines.append("mov dword ptr fs:[edi+%d], %d" % (0x1000 + 4 * lp, (pp << 12) | 3))
    lines += ["mov eax, 0x10000", "mov cr3, eax", "mov eax, cr0", "or eax, 0x80000000", "mov cr0, eax"]
    return lines


def wrap(program, init, bits32=True, paged=False):
    """Return assembly text; `program` is the generator's body (ending in hlt)."""
    regs = init["regs"]
    flags = init.get("flags", {})
    efl = 2
    for f, bit in (("CF", 0), ("PF", 2), ("AF", 4), ("ZF", 6), ("SF", 7), ("DF", 10), ("OF", 11)):
        if flags.get(f):
            efl |= 1 << bit
    lines = [
        "cli",
        "lgdt cs:[gdtr]",
        "mov eax, cr0",
        "or al, 1",
        "mov cr0, eax",
        ".byte 0xEA",
        ".word pm_entry",
        ".word 0x08",
        ".align 4",
        "gdtr: .word %d" % (len(GDT) * 8 - 1),
        ".long gdt",
        ".align 8",
        "gdt:",
    ] + GDT + [
        "pm_entry:",
    ] + (paging_setup() if paged else []) + [
        "mov ax, 0x10", "mov ds, ax", "mov ax, 0x18", "mov es, ax", "mov ax, 0x28", "mov fs, ax", "mov gs, ax",
        "mov ax, 0x20", "mov ss, ax",
        "mov esp, %d" % (regs.get("ESP", 0x1000)),
    ]
    for n in ("EAX", "ECX", "EDX", "EBX", "EBP", "ESI", "EDI"):
        lines.append("mov %s, %d" % (n.lower(), regs.get(n, 0)))
    lines += [".byte 0x66, 0x68", ".long %d" % efl, "popfd"]   # push imm32 / popfd with 32-bit operand size in 16-bit code
    lines.append(program)
    return "\n".join(lines)
