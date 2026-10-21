"""Reference execution via Unicorn (QEMU-based CPU emulator).

TEST-ONLY.  Used to compute expected results for differential testing; it is
never part of the Scratch86 runtime.
"""
from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UcError
from unicorn.x86_const import *

GPRS = [("EAX", UC_X86_REG_EAX), ("ECX", UC_X86_REG_ECX), ("EDX", UC_X86_REG_EDX),
        ("EBX", UC_X86_REG_EBX), ("ESP", UC_X86_REG_ESP), ("EBP", UC_X86_REG_EBP),
        ("ESI", UC_X86_REG_ESI), ("EDI", UC_X86_REG_EDI)]
SEGS = [("ES", UC_X86_REG_ES), ("CS", UC_X86_REG_CS), ("SS", UC_X86_REG_SS),
        ("DS", UC_X86_REG_DS), ("FS", UC_X86_REG_FS), ("GS", UC_X86_REG_GS)]
FLAGBITS = {"CF": 0, "PF": 2, "AF": 4, "ZF": 6, "SF": 7, "TF": 8, "IF": 9, "DF": 10, "OF": 11}
LOAD = 0x7C00


def run_reference(code, init, peek, max_insns=10000):
    uc = Uc(UC_ARCH_X86, UC_MODE_16)
    uc.mem_map(0, 0x200000)
    uc.mem_write(LOAD, bytes(code))
    for m in init.get("mem", []):
        uc.mem_write(m["addr"], bytes(m["bytes"]))
    regs = init.get("regs", {})
    for name, rid in GPRS:
        uc.reg_write(rid, regs.get(name, 0))
    for name, rid in SEGS:
        uc.reg_write(rid, init.get("seg", {}).get(name, 0))
    efl = 2
    for f, bit in FLAGBITS.items():
        if init.get("flags", {}).get(f, 0):
            efl |= 1 << bit
    uc.reg_write(UC_X86_REG_EFLAGS, efl)
    end = LOAD + len(code) - 1            # address of the trailing HLT
    try:
        uc.emu_start(LOAD, end, count=max_insns)
    except UcError as e:
        return {"error": str(e)}
    res = {"regs": {n: uc.reg_read(r) for n, r in GPRS}}
    res["seg"] = {n: uc.reg_read(r) for n, r in SEGS}
    e = uc.reg_read(UC_X86_REG_EFLAGS)
    res["flags"] = {f: (e >> b) & 1 for f, b in FLAGBITS.items()}
    res["eip"] = uc.reg_read(UC_X86_REG_EIP)
    res["mem"] = [{"addr": pk["addr"], "bytes": list(uc.mem_read(pk["addr"], pk["len"]))} for pk in peek]
    return res
