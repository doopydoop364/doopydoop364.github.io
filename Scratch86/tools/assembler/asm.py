"""Tiny wrapper around GNU as/objcopy: assemble Intel-syntax x86 into raw bytes.

Development-time tool only.  GNU as is an independent assembler, so machine
code in the tests is not produced by code that shares logic with the emulator.
"""
import os
import subprocess
import tempfile


def assemble(src, bits=16, org=0x7C00):
    """Assemble `src` (Intel syntax, no leading directives needed) -> bytes.

    Linked at `org` so absolute label references (``offset msg``) resolve."""
    prelude = ".intel_syntax noprefix\n.code%d\n" % bits
    with tempfile.TemporaryDirectory() as d:
        s, o, b = (os.path.join(d, n) for n in ("a.s", "a.o", "a.bin"))
        with open(s, "w") as f:
            f.write(prelude + src + "\n")
        r = subprocess.run(["as", "--32", "-o", o, s], capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError("assembler error:\n" + r.stderr + "\nsource:\n" + src)
        e = os.path.join(d, "a.elf")
        r = subprocess.run(["ld", "-m", "elf_i386", "-Ttext", hex(org), "-e", "0", "-o", e, o],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError("linker error:\n" + r.stderr)
        subprocess.run(["objcopy", "-O", "binary", "-j", ".text", e, b], check=True)
        with open(b, "rb") as f:
            return f.read()


if __name__ == "__main__":
    import sys
    print(assemble(sys.stdin.read()).hex())
