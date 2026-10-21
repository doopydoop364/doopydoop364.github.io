"""Tiny wrapper around GNU as/objcopy: assemble Intel-syntax x86 into raw bytes.

Development-time tool only.  GNU as is an independent assembler, so machine
code in the tests is not produced by code that shares logic with the emulator.
"""
import os
import subprocess
import tempfile


def assemble(src, bits=16):
    """Assemble `src` (Intel syntax, no leading directives needed) -> bytes."""
    prelude = ".intel_syntax noprefix\n.code%d\n" % bits
    with tempfile.TemporaryDirectory() as d:
        s, o, b = (os.path.join(d, n) for n in ("a.s", "a.o", "a.bin"))
        with open(s, "w") as f:
            f.write(prelude + src + "\n")
        subprocess.run(["as", "--32", "-o", o, s], check=True, capture_output=True)
        subprocess.run(["objcopy", "-O", "binary", "-j", ".text", o, b], check=True)
        with open(b, "rb") as f:
            return f.read()


if __name__ == "__main__":
    import sys
    print(assemble(sys.stdin.read()).hex())
