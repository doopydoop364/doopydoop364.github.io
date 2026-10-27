"""Declares every global Scratch variable and list used by the emulator.

All emulator state lives on the Stage as "for all sprites" variables/lists so
every script (CPU, devices, display, UI) can reach it.  Names are the names a
person sees in the Scratch editor, so they are kept descriptive.

Conventions
-----------
* Guest-visible 32-bit quantities are stored as *unsigned* integers in 0..2^32-1
  (exactly representable in a Scratch double).
* Flags are stored as individual 0/1 numbers (never Scratch booleans) so they
  can be used directly in arithmetic.
* Lists are 1-based (Scratch).  Register / segment *indices* in the emulator
  are 0-based x86 encodings; `item (idx + 1) of GPR` converts.
"""


class Namespace:
    pass


RAM_BANKS = 17          # 16 x 64 KiB (1 MiB) + 64 KiB HMA window = 1088 KiB
BANK_SIZE = 65536
PAR_TAB = [1 - (bin(i).count("1") & 1) for i in range(256)]   # 1 = even parity


def _and_table():
    return [i & j for i in range(256) for j in range(256)]


def declare(p, ram_banks=RAM_BANKS):
    S = Namespace()
    v = p.var

    # ---- architectural CPU state -------------------------------------------
    # GPR order is the x86 encoding order: EAX ECX EDX EBX ESP EBP ESI EDI.
    # Item 9 is a constant zero used by the 16-bit effective-address tables.
    S.GPR = p.list("GPR [EAX ECX EDX EBX ESP EBP ESI EDI zero]", [0] * 9)
    S.EIP = v("EIP")
    # Segment registers, encoding order: ES CS SS DS FS GS.
    S.SEG = p.list("SEG [ES CS SS DS FS GS]", [0] * 6)
    S.SEGBASE = p.list("SEGBASE [ES CS SS DS FS GS] (hidden base)", [0] * 6)
    for f in ("CF", "PF", "AF", "ZF", "SF", "TF", "IF", "DF", "OF"):
        setattr(S, f, v(f))
    S.EFL_OTHER = v("EFLAGS other bits (NT IOPL RF VM AC ...)")
    S.CR0 = v("CR0")
    S.CR2 = v("CR2")
    S.CR3 = v("CR3")
    S.CPU_MODE = v("CPU_MODE (0=real 1=prot16 2=prot32 3=v86)")
    S.A20 = v("A20 gate enabled")
    S.DEF_OSZ = v("default operand size (bytes)", 2)
    S.DEF_ASZ = v("default address size (bytes)", 2)
    S.STK32 = v("stack is 32-bit (SS.B)")
    S.CPL = v("current privilege level")
    S.CR4 = v("CR4")
    S.PROFILE = v("CPU profile (0=8086 1=286 3=386 4=486)", 3)
    # hidden segment-descriptor cache, in addition to SEG / SEGBASE / SEGLIM:
    #   SEGATTR = access byte + 256*(AVL L D/B G);  SEGPERM bit0 usable, bit1 readable, bit2 writable, bit3 expand-down
    S.SEGATTR = p.list("SEGATTR [ES CS SS DS FS GS] (access + 256*flags)", [0x93] * 6)
    S.SEGPERM = p.list("SEGPERM [ES CS SS DS FS GS] (1 use 2 rd 4 wr 8 expand-down)", [7] * 6)
    S.GDTR_BASE = v("GDTR base")
    S.GDTR_LIM = v("GDTR limit")
    S.IDTR_BASE = v("IDTR base")
    S.IDTR_LIM = v("IDTR limit", 1023)
    S.LDTR_SEL = v("LDTR selector")
    S.LDTR_BASE = v("LDTR base")
    S.LDTR_LIM = v("LDTR limit")
    S.LDTR_ATTR = v("LDTR attributes")
    S.TR_SEL = v("TR selector")
    S.TR_BASE = v("TR base")
    S.TR_LIM = v("TR limit")
    S.TR_ATTR = v("TR attributes")
    S.D_LO = v("descriptor low dword")
    S.D_HI = v("descriptor high dword")
    S.D_BASE = v("descriptor base")
    S.D_LIM = v("descriptor limit (bytes-1)")
    S.D_ATTR = v("descriptor attributes")
    S.D_LIN = v("descriptor linear address")
    S.D_OK = v("descriptor fetch ok")
    S.CODE_MASK = v("EIP wrap modulus", 65536)

    # ---- CPU run control ----------------------------------------------------
    S.CPU_STATE = v("CPU_STATE (0=off 1=run 2=halted 3=fault)")
    S.FAULT_VEC = v("FAULT vector (6=#UD 0=#DE)")
    S.TF_START = v("TF at start of the current instruction")
    S.FAULT_PEND = v("pending exception vector (-1 none)", -1)
    S.FAULT_ERR = v("pending exception error code")
    S.FAULT_TRAP = v("pending exception is a trap (EIP = next instruction)")
    S.STOP_UNSET = v("diag: stop CPU when exception vector is unset (IVT 0000:0000)", 1)
    S.SEGLIM = p.list("SEGLIM [ES CS SS DS FS GS] (hidden limit)", [65535] * 6)
    S.FAULT_OPC = v("FAULT opcode")
    S.FAULT_EIP = v("FAULT instruction EIP")
    S.INSN_COUNT = v("executed instructions")
    S.BATCH_LEFT = v("batch instructions left")
    S.REQ = v("REQ")
    S.BUDGET = v("instruction budget per frame", 5000)

    # ---- decode scratch -------------------------------------------------------
    S.EIP_START = v("EIP of current instruction")
    S.OPCODE = v("OPCODE")
    S.PFX_SEG = v("prefix segment (-1 none)", -1)
    S.PFX_REP = v("prefix REP (0 none, 1=F3, 2=F2)")
    S.PFX_LOCK = v("prefix LOCK")
    S.PFX_MORE = v("prefix loop flag")
    S.PFX_COUNT = v("prefix byte count")
    S.OSZ = v("operand size (bytes)", 2)
    S.ASZ = v("address size (bytes)", 2)
    S.OPSZ = v("current op width (bytes)", 2)
    S.OP_M = v("current op modulus", 65536)
    S.OP_SGN = v("current op sign bit", 32768)
    S.MODRM = v("MODRM")
    S.MOD = v("MOD")
    S.REG = v("REG")
    S.RM = v("RM")
    S.EA_REG = v("EA is register")
    S.EA_SEG = v("EA segment")
    S.EA_OFF = v("EA offset")
    S.EA_LIN = v("EA linear")
    S.SIB = v("SIB")
    S.FB = v("fetched byte")
    S.FW = v("fetched word")
    S.FLIN = v("fetch linear")
    S.IMM = v("immediate")
    S.RV = v("register value")
    S.RMV = v("r/m value")
    S.TA = v("temp A")
    S.TB = v("temp B")
    S.TC = v("temp C")
    S.TD = v("temp D")
    S.ALU_R = v("ALU result")
    S.ANDR = v("bytewise AND result")
    S.COND = v("condition result")
    S.EFL = v("EFLAGS packed")
    S.SPW = v("stack pointer work")
    S.PV = v("popped value")
    S.SCALE = p.list("SIB scale", [1, 2, 4, 8])
    S.H1 = v("handler temp 1")
    S.H2 = v("handler temp 2")
    S.H3 = v("handler temp 3")
    S.H4 = v("handler temp 4")
    S.POW2 = p.list("POW2 (2^0 .. 2^34)", [2 ** i for i in range(35)])
    for i in range(1, 9):
        setattr(S, "X%d" % i, v("math temp %d" % i))
    S.ML = v("mul/div result low")
    S.MH = v("mul/div result high")
    S.MFIT = v("signed multiply fits in one operand")
    S.QQ = v("division quotient")
    S.RR = v("division remainder")
    S.IOV = v("I/O port read value")
    S.SZ_M = p.list("SIZE modulus by bytes", [256, 65536, 16777216, 4294967296])
    S.SZ_SGN = p.list("SIZE sign bit by bytes", [128, 32768, 8388608, 2147483648])

    # ---- memory subsystem ------------------------------------------------------
    S.RAM = [p.list("RAM_%02d" % i, [0] * BANK_SIZE) for i in range(ram_banks)]
    S.RAM_BANKS = ram_banks
    S.PHYS = v("physical address")
    S.BANK = v("RAM bank")
    S.BOFF = v("offset in bank (1-based)")
    S.MEMV = v("memory value")
    S.MEMT = v("memory temp")
    S.ANDTAB = p.list("ANDTAB (256x256 byte AND)", _and_table())
    S.PAR = p.list("PARITY (1=even)", PAR_TAB)

    # decode tables (ROM-like constants)
    S.EA16_B1 = p.list("EA16 base1 (GPR idx)", [4, 4, 6, 6, 7, 8, 6, 4])
    S.EA16_B2 = p.list("EA16 base2 (GPR idx)", [7, 8, 7, 8, 9, 9, 9, 9])
    S.EA16_SEG = p.list("EA16 default seg", [3, 3, 2, 2, 3, 3, 2, 3])
    # Prefix kinds: 1 segment override, 2 opsize, 3 addrsize, 4 lock, 5 rep(F3), 6 repne(F2)
    pk = [0] * 256
    pseg = [-1] * 256
    for op, seg in ((0x26, 0), (0x2E, 1), (0x36, 2), (0x3E, 3), (0x64, 4), (0x65, 5)):
        pk[op], pseg[op] = 1, seg
    pk[0x66], pk[0x67], pk[0xF0], pk[0xF3], pk[0xF2] = 2, 3, 4, 5, 6
    S.PFXKIND = p.list("PREFIX kind by opcode", pk)
    S.PFXSEG = p.list("PREFIX segment by opcode", pseg)

    # ---- diagnostics -----------------------------------------------------------
    S.DIAG_MEM = v("diag: record memory accesses")
    S.LAST_FETCH = v("last instruction fetch (linear)")
    S.LAST_OPCODE = v("last opcode")
    S.LAST_RD_LIN = v("last read linear")
    S.LAST_RD_PHYS = v("last read physical")
    S.LAST_WR_LIN = v("last write linear")
    S.LAST_WR_PHYS = v("last write physical")
    S.LAST_MEM_VAL = v("last data access value")
    S.LAST_MEM_W = v("last data access width")
    S.LAST_MEM_RW = v("last data access kind (0=none 1=read 2=write)")
    S.LAST_EA_SEG = v("last logical segment")
    S.LAST_EA_OFF = v("last logical offset")
    S.DIAG_TRACE = v("diag: record instruction trace")
    S.TRACE_POS = v("trace ring position")
    S.TRACE_CS = p.list("TRACE CS", [0] * 8)
    S.TRACE_IP = p.list("TRACE EIP", [0] * 8)
    S.TRACE_OP = p.list("TRACE opcode", [0] * 8)

    # ---- boot / test image -----------------------------------------------------
    S.BOOT_IMAGE = p.list("BOOT_IMAGE (loaded at 0000:7C00)", [])
    S.TEST_LIMIT = v("test: instruction limit", 100000)
    S.TEST_DONE = v("test: finished")
    S.TEST_OP = v("test: memory op (1-3 read b/w/d, 4-6 write b/w/d)")
    S.TEST_ADDR = v("test: memory address")
    S.TEST_VAL = v("test: memory value")
    S.TEST_RES = v("test: memory result")
    return S
