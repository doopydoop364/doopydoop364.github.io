"""Deterministic benchmark workloads (real x86 machine code, assembled with GNU as)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assembler"))
from asm import assemble  # noqa: E402

# 6 instructions per iteration: ALU, memory store, memory load, ALU, DEC, JNZ.
MIXED = """
        mov ecx, {n}
        xor ax, ax
top:    add ax, 3
        mov word ptr [0x1000], ax
        mov bx, word ptr [0x1000]
        xor bx, ax
        dec ecx
        jnz top
        hlt
"""
# 4 instructions per iteration, no memory data access.
ALU = """
        mov ecx, {n}
top:    add ax, 7
        sub bx, ax
        dec ecx
        jnz top
        hlt
"""


def build(name, n):
    return assemble({"mixed": MIXED, "alu": ALU}[name].format(n=n))


if __name__ == "__main__":
    out = sys.argv[1]
    for name, n in (("mixed", int(sys.argv[2])), ("alu", int(sys.argv[2]))):
        with open(os.path.join(out, "bench_%s.bin" % name), "wb") as f:
            f.write(build(name, n))
