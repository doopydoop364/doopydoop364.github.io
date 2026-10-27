"""Random x86 test-program generator for differential testing.

Emits Intel-syntax assembly (assembled by GNU as) restricted to the
instruction subset Scratch86 implements.  Programs are short straight-line
sequences with a few forward branches; memory operands are kept inside small
preloaded windows so that results are comparable with the reference emulator.
"""
import random

R8 = ["al", "cl", "dl", "bl", "ah", "ch", "dh", "bh"]
R16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
R32 = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi"]
SEGS_USED = {"ds": 0x1000, "es": 0x2000, "ss": 0x3000}
ALU = ["add", "or", "adc", "sbb", "and", "sub", "xor", "cmp"]
CC = ["o", "no", "b", "ae", "e", "ne", "be", "a", "s", "ns", "p", "np", "l", "ge", "le", "g"]
INTERESTING = [0, 1, 2, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF, 0x10000, 0x7FFFFFFF,
               0x80000000, 0xFFFFFFFF, 0x12345678, 0xDEADBEEF]


ARITH = ["CF", "PF", "AF", "ZF", "SF", "OF"]


class Gen:
    def __init__(self, seed, allow32=True, ext=True):
        self.r = random.Random(seed)
        self.allow32 = allow32
        self.ext = ext
        self.label = 0
        self.mask = set()       # flags left architecturally undefined by the final (special) instruction

    def val(self, bits):
        r = self.r
        if r.random() < 0.5:
            v = r.choice(INTERESTING)
        else:
            v = r.getrandbits(bits)
        return v & ((1 << bits) - 1)

    def sval(self, bits):
        """Immediate as a signed number for assembler friendliness."""
        v = self.val(bits)
        return v

    def reg(self, bits):
        return {8: R8, 16: R16, 32: R32}[bits][self.r.randrange(8)]

    def mem(self, bits=None):
        r = self.r
        seg = r.choice(["", "", "", "ds:", "es:", "ss:"])
        if self.allow32 and r.random() < 0.3:
            base = r.choice(["eax", "ebx", "ecx", "edx", "esi", "edi", "ebp"])
            if r.random() < 0.5:
                idx = r.choice(["eax", "ebx", "ecx", "edx", "esi", "edi", "ebp"])
                sc = r.choice([1, 2, 4, 8])
                ea = "%s+%s*%d" % (base, idx, sc)
            else:
                ea = base
            if r.random() < 0.6:
                ea += "%+d" % r.randrange(-100, 200)
            if r.random() < 0.1:
                ea = "esp+%d" % r.randrange(0, 64)
        else:
            ea = r.choice(["bx+si", "bx+di", "bp+si", "bp+di", "si", "di", "bx", "bp"])
            if r.random() < 0.6:
                ea += "%+d" % r.randrange(-100, 200 if r.random() < 0.7 else 300)
            if r.random() < 0.08:
                ea = "%d" % r.randrange(0, 0x200)
        return "%s[%s]" % (seg, ea)

    def ptr(self, bits):
        return {8: "byte ptr ", 16: "word ptr ", 32: "dword ptr "}[bits]

    def bits(self):
        return self.r.choice([8, 16, 16, 32] if self.allow32 else [8, 16])

    def rm(self, bits):
        if self.r.random() < 0.5:
            return self.reg(bits)
        return self.ptr(bits) + self.mem()

    def one(self):
        r = self.r
        pool = ["alu"] * 6 + ["incdec", "negnot", "test", "mov", "mov", "xchg", "lea", "stack",
                              "flags", "cbw", "branch", "loop", "movimm"]
        if self.ext:
            pool += ["movx", "movx", "setcc", "bswap", "xaddcx", "pusha", "enterleave"]
        k = r.choice(pool)
        bits = self.bits()
        if k == "alu":
            op = r.choice(ALU)
            form = r.choice(["rr", "rm", "mr", "ri", "mi", "ai"])
            if form == "rr":
                return "%s %s, %s" % (op, self.reg(bits), self.reg(bits))
            if form == "rm":
                return "%s %s, %s" % (op, self.reg(bits), self.ptr(bits) + self.mem())
            if form == "mr":
                return "%s %s%s, %s" % (op, self.ptr(bits), self.mem(), self.reg(bits))
            if form == "ri":
                return "%s %s, %d" % (op, self.reg(bits), self.val(bits))
            if form == "mi":
                return "%s %s%s, %d" % (op, self.ptr(bits), self.mem(), self.val(bits))
            return "%s %s, %d" % (op, {8: "al", 16: "ax", 32: "eax"}[bits], self.val(bits))
        if k == "incdec":
            return "%s %s" % (r.choice(["inc", "dec"]), self.rm(bits))
        if k == "negnot":
            return "%s %s" % (r.choice(["neg", "not"]), self.rm(bits))
        if k == "test":
            f = r.choice(["rr", "mr", "ri", "ai"])
            if f == "rr":
                return "test %s, %s" % (self.reg(bits), self.reg(bits))
            if f == "mr":
                return "test %s%s, %s" % (self.ptr(bits), self.mem(), self.reg(bits))
            if f == "ri":
                return "test %s, %d" % (self.reg(bits), self.val(bits))
            return "test %s, %d" % ({8: "al", 16: "ax", 32: "eax"}[bits], self.val(bits))
        if k == "mov":
            f = r.choice(["rr", "rm", "mr"])
            if f == "rr":
                return "mov %s, %s" % (self.reg(bits), self.reg(bits))
            if f == "rm":
                return "mov %s, %s%s" % (self.reg(bits), self.ptr(bits), self.mem())
            return "mov %s%s, %s" % (self.ptr(bits), self.mem(), self.reg(bits))
        if k == "movimm":
            f = r.choice(["ri", "mi", "moffs"])
            if f == "ri":
                return "mov %s, %d" % (self.reg(bits), self.val(bits))
            if f == "mi":
                return "mov %s%s, %d" % (self.ptr(bits), self.mem(), self.val(bits))
            acc = {8: "al", 16: "ax", 32: "eax"}[bits]
            seg = r.choice(["", "es:", "ss:"])
            a = r.randrange(0, 0x300)
            if r.random() < 0.5:
                return "mov %s, %s%s[%d]" % (acc, self.ptr(bits), seg, a)
            return "mov %s%s[%d], %s" % (self.ptr(bits), seg, a, acc)
        if k == "xchg":
            if r.random() < 0.5:
                return "xchg %s, %s" % (self.reg(bits), self.reg(bits))
            if r.random() < 0.5:
                return "xchg %s%s, %s" % (self.ptr(bits), self.mem(), self.reg(bits))
            b = r.choice([16, 32] if self.allow32 else [16])
            return "xchg %s, %s" % ({16: "ax", 32: "eax"}[b], self.reg(b))
        if k == "lea":
            b = r.choice([16, 32] if self.allow32 else [16])
            return "lea %s, %s" % (self.reg(b), self.mem().split(":")[-1].strip("[]").join("[]"))
        if k == "stack":
            b = r.choice([16, 32] if self.allow32 else [16])
            c = r.choice(["push", "pop", "pushimm", "pushf", "popf", "pushmem", "popmem"])
            if c == "push":
                return "push %s" % self.reg(b)
            if c == "pop":
                return "pop %s" % self.reg(b)
            if c == "pushimm":
                if b == 16:
                    return "pushw %d" % (self.val(16) if r.random() < 0.5 else r.randrange(-128, 128))
                if r.random() < 0.5:
                    return ".byte 0x66, 0x68\n.long %d" % self.val(32)
                return ".byte 0x66, 0x6a, %d" % r.randrange(0, 256)
            if c == "pushf":
                return {16: "pushf", 32: "pushfd"}[b]
            if c == "popf":
                return {16: "popf", 32: "popfd"}[b]
            if c == "pushmem":
                return "push %s%s" % (self.ptr(b), self.mem())
            return "pop %s%s" % (self.ptr(b), self.mem())
        if k == "flags":
            return r.choice(["clc", "stc", "cmc", "cld", "std", "lahf", "sahf"])
        if k == "cbw":
            return r.choice(["cbw", "cwde", "cwd", "cdq"] if self.allow32 else ["cbw", "cwd"])
        if k == "branch":
            self.label += 1
            l = "L%d" % self.label
            body = self.one_simple()
            if r.random() < 0.3:
                return "jmp %s\n%s\n%s:" % (l, body, l)
            return "j%s %s\n%s\n%s:" % (r.choice(CC), l, body, l)
        if k == "loop":
            self.label += 1
            l = "L%d" % self.label
            # LOOPNE/LOOPE are excluded: Unicorn 2.1.4 (QEMU) corrupts CF/AF around them
            # (it reports CF=AF=0 after `sub`), so it cannot serve as a reference.
            # LOOPNE is covered by deterministic tests in tests/cpu instead.
            kind = r.choice(["loop", "jcxz"])      # LOOPE also hits the Unicorn lazy-flag quirk (seeds 1044/1119/1586)
            if kind == "jcxz":
                return "mov cx, %d\njcxz %s\n%s\n%s:" % (r.randrange(0, 3), l, self.one_simple(), l)
            return "mov cx, %d\n%s:\n%s\n%s %s" % (r.randrange(1, 5), l, self.one_simple(), kind, l)
        if k == "movx":
            op = r.choice(["movzx", "movsx"])
            dst = r.choice([16, 32] if self.allow32 else [16])
            src = r.choice([8, 16]) if dst == 32 else 8
            srcop = self.reg(src) if r.random() < 0.5 else self.ptr(src) + self.mem()
            return "%s %s, %s" % (op, self.reg(dst), srcop)
        if k == "setcc":
            return "set%s %s" % (r.choice(CC), self.rm(8))
        if k == "bswap":
            return "bswap %s" % self.reg(32)
        if k == "xaddcx":
            op = r.choice(["xadd", "cmpxchg"])
            b = self.bits()
            return "%s %s, %s" % (op, self.rm(b), self.reg(b))
        if k == "pusha":
            return r.choice(["pushaw", "popaw"] + (["pushad", "popad"] if self.allow32 else []))
        if k == "enterleave":
            if r.random() < 0.5:
                return "leave"
            return "enter %d, %d" % (r.randrange(0, 16) * 2, r.randrange(0, 3))
        raise AssertionError(k)

    def one_simple(self):
        """Straight-line instruction that cannot disturb loop control (CX, flags used by LOOPcc ok)."""
        for _ in range(20):
            s = self.one()
            if "\n" in s or "cx" in s or "loop" in s or "ecx" in s or "cl" in s or "ch" in s:
                continue
            if s.split()[0] in ("pushf", "popf", "pushfd", "popfd", "popaw", "popad"):   # popa* loads CX from memory
                continue
            return s
        return "nop"

    def program(self, n=None):
        n = n or self.r.randrange(1, 6)
        parts = [self.one() for _ in range(n)]
        if self.ext and self.r.random() < 0.6:
            parts.append(self.special())
        return "\n".join(parts) + "\nhlt"

    # ---- instructions with architecturally undefined flags / faults (always last) ----
    def special(self):
        r = self.r
        k = r.choice(["mul", "mul", "imul", "div", "div", "shift", "shift", "shift", "shxd", "bt", "bsx",
                      "bcd", "string", "string", "string", "bound", "xlat", "lxs", "int"])
        bits = self.bits()
        acc = {8: "al", 16: "ax", 32: "eax"}[bits]
        if k == "mul":
            self.mask |= {"SF", "ZF", "AF", "PF"}
            return "%s %s" % (r.choice(["mul", "imul"]), self.rm(bits))
        if k == "imul":
            self.mask |= {"SF", "ZF", "AF", "PF"}
            b = r.choice([16, 32] if self.allow32 else [16])
            f = r.choice(["rr", "rm", "rri", "rmi8"])
            if f == "rr":
                return "imul %s, %s" % (self.reg(b), self.reg(b))
            if f == "rm":
                return "imul %s, %s%s" % (self.reg(b), self.ptr(b), self.mem())
            if f == "rri":
                return "imul %s, %s, %d" % (self.reg(b), self.reg(b), self.val(b) if b == 16 else r.randrange(-2**31, 2**31))
            return "imul %s, %s%s, %d" % (self.reg(b), self.ptr(b), self.mem(), r.randrange(-128, 128))
        if k == "div":
            self.mask |= set(ARITH)
            op = r.choice(["div", "idiv"])
            pre = ""
            if r.random() < 0.7:               # usually give it a sane dividend/divisor
                ext = {8: "xor ah, ah" if op == "div" else "cbw", 16: "xor dx, dx" if op == "div" else "cwd",
                       32: "xor edx, edx" if op == "div" else "cdq"}[bits]
                pre = "%s\n" % ext
                if r.random() < 0.7:
                    pre += "mov %s, %d\n" % ({8: "bl", 16: "bx", 32: "ebx"}[bits], max(1, self.val(bits)) if r.random() < 0.8 else r.randrange(1, 4))
                    return pre + "%s %s" % (op, {8: "bl", 16: "bx", 32: "ebx"}[bits])
            return pre + "%s %s" % (op, self.rm(bits))
        if k == "shift":
            op = r.choice(["shl", "shr", "sar", "rol", "ror", "rcl", "rcr", "sal"])
            kind = r.choice(["imm", "one", "cl"])
            size = bits
            if kind == "one":
                cnt = 1
            else:
                cnt = r.choice([0, 1, 2, 3, 7, 8, 9, 15, 16, 17, 31, 32, 33, 255, r.randrange(0, 40)])
            c = cnt % 32
            self.mask.add("AF") if op in ("shl", "shr", "sar", "sal") else None
            if c != 1 and kind != "one":
                self.mask.add("OF")
            if op in ("shl", "shr", "sar", "sal") and c > size:
                self.mask.add("CF")
            if op in ("rol", "ror") and c % size == 0 and False:
                pass
            if kind == "cl":
                return "mov cl, %d\n%s %s, cl" % (cnt, op, self.rm(bits))
            if kind == "one":
                return "%s %s, 1" % (op, self.rm(bits))
            return "%s %s, %d" % (op, self.rm(bits), cnt)
        if k == "shxd":
            self.mask |= {"AF"}
            b = r.choice([16, 32] if self.allow32 else [16])
            cnt = r.randrange(1, b)
            if cnt != 1:
                self.mask.add("OF")
            op = r.choice(["shld", "shrd"])
            if r.random() < 0.5:
                return "%s %s, %s, %d" % (op, self.rm(b), self.reg(b), cnt)
            return "mov cl, %d\n%s %s, %s, cl" % (cnt, op, self.rm(b), self.reg(b))
        if k == "bt":
            self.mask |= {"OF", "SF", "AF", "PF"}
            b = r.choice([16, 32] if self.allow32 else [16])
            op = r.choice(["bt", "bts", "btr", "btc"])
            if r.random() < 0.5:
                return "%s %s, %d" % (op, self.rm(b), r.randrange(0, 256))
            # register bit offset: keep the effective address inside the preloaded window
            off = r.randrange(-40, 120)
            tmp = "esi" if b == 32 else "si"
            return "mov %s, %d\n%s %s%s, %s" % (tmp, off & ((1 << b) - 1), op, self.ptr(b), self.mem().replace("si", "bp") if False else self.mem_nosi(), tmp)
        if k == "bsx":
            self.mask |= {"OF", "SF", "AF", "PF", "CF"}
            b = r.choice([16, 32] if self.allow32 else [16])
            src = {16: "cx", 32: "ecx"}[b]
            val = (self.val(b) | (1 << r.randrange(0, b)))
            return "mov %s, %d\n%s %s, %s" % (src, val, r.choice(["bsf", "bsr"]), self.reg(b), src)
        if k == "bcd":
            which = r.choice(["daa", "das", "aaa", "aas", "aam", "aad"])
            self.mask |= {"daa": {"OF"}, "das": {"OF"}, "aaa": {"OF", "SF", "ZF", "PF"}, "aas": {"OF", "SF", "ZF", "PF"},
                          "aam": {"OF", "AF", "CF"}, "aad": {"OF", "AF", "CF"}}[which]
            if which in ("aam", "aad"):
                return "%s %d" % (which, r.choice([10, 10, r.randrange(1, 256), 0 if which == "aam" and r.random() < 0.2 else 7]))
            return which
        if k == "string":
            op = r.choice(["movs", "cmps", "stos", "lods", "scas"])
            sz = r.choice(["b", "w", "d"] if self.allow32 else ["b", "w"])
            rep = r.choice(["", "rep ", "repe ", "repne "]) if op in ("cmps", "scas") else r.choice(["", "rep "])
            n = r.randrange(0, 6)
            setup = "cld\n" if r.random() < 0.5 else "std\n"
            a32 = r.random() < 0.2 and self.allow32
            si, di, cx = ("esi", "edi", "ecx") if a32 else ("si", "di", "cx")
            setup += "mov %s, %d\nmov %s, %d\nmov %s, %d\n" % (si, r.randrange(0x50, 0xB0), di, r.randrange(0x50, 0xB0), cx, n)
            if a32:
                setup += ".byte 0x67\n"
            seg = r.choice(["", "", "es:", "ss:"]) if op in ("movs", "cmps", "lods") else ""
            if seg:
                setup += {"es:": ".byte 0x26\n", "ss:": ".byte 0x36\n"}[seg]
            return setup + "%s%s%s" % (rep, op, sz)
        if k == "bound":
            b = r.choice([16, 32] if self.allow32 else [16])
            return "bound %s, %s%s" % (self.reg(b), "dword ptr " if b == 16 else "qword ptr ", self.mem())
        if k == "xlat":
            return "xlatb"
        if k == "lxs":
            b = r.choice([16, 32] if self.allow32 else [16])
            return "%s %s, %s" % (r.choice(["les", "lds", "lss", "lfs", "lgs"]), self.reg(b), ("dword ptr " if b == 16 else "fword ptr ") + self.mem())
        if k == "int":
            return r.choice(["int3", "int 0x21", "int 0", "into", "int 255"])
        raise AssertionError(k)

    def mem_nosi(self):
        """Memory operand that does not use SI/ESI (they hold the bit offset)."""
        for _ in range(50):
            m = self.mem()
            if "si" not in m:
                return m
        return "[bx]"

    def init_state(self):
        r = self.r
        regs = {}
        for n in ["EAX", "ECX", "EDX", "ESI", "EDI", "EBX", "EBP"]:
            if n in ("EBX", "ESI", "EDI", "EBP"):
                regs[n] = r.randrange(0, 0x80)          # address registers: small
            else:
                regs[n] = self.val(32)
        regs["ESP"] = 0x1000 + r.choice([0, 2, 4])
        flags = {f: r.randrange(2) for f in ("CF", "PF", "AF", "ZF", "SF", "OF", "DF")}
        flags["IF"] = 0
        flags["TF"] = 0
        seg = dict((k.upper(), v) for k, v in SEGS_USED.items())
        mem = []
        windows = [(0x10000, 0x400), (0x20000, 0x400), (0x30000, 0x400), (0x30C00, 0x500)]
        for base, ln in windows:
            mem.append({"addr": base, "bytes": [r.getrandbits(8) for _ in range(ln)]})
        return {"regs": regs, "flags": flags, "seg": seg, "mem": mem}

    PEEK = [{"addr": 0x10000, "len": 0x400}, {"addr": 0x20000, "len": 0x400},
            {"addr": 0x30000, "len": 0x400}, {"addr": 0x30C00, "len": 0x500}]
