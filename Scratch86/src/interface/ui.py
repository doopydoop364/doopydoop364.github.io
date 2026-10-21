"""Interface layer (Stage side): settings menu, diagnostic overlays, controls.

The Stage never draws; it fills the 80x25 `UI_BUF` text layer with
`code + 256*attribute` cells (-1 = transparent).  The Screen sprite
(src/display/screen.py) composes `UI_BUF` over guest video memory and stamps
only the cells that changed, so closing the menu or an overlay automatically
restores the guest screen underneath.

Control requests (`REQ`) are *consumed by the CPU loop between instructions*;
no UI script ever mutates CPU state directly.

    REQ 1  power toggle    2  reset    3  step one instruction    4  pause toggle
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403
from sb3gen import Op  # noqa: E402


def round_(x):
    return Op("operator_round", {"NUM": x if not isinstance(x, (int, float)) else x})

ATTR_OV = 0x0A       # overlay text: bright green on black
ATTR_MENU = 0x07
ATTR_ON = 0x0A
ATTR_TITLE = 0x70
ATTR_ERR = 0x4F      # white on red

# (label, needs_memory_diag) in the order of the settings menu / DIAG_ON list.
DIAG_ITEMS = [
    "VIRTUAL CPU FREQUENCY (IPS)",       # 1
    "CUMULATIVE INSTRUCTIONS",           # 2
    "CURRENT CS:EIP",                    # 3
    "LAST FETCHED INSTRUCTION ADDR",     # 4
    "LAST FETCHED INSTRUCTION BYTES",    # 5
    "LAST DECODED OPCODE",               # 6
    "LAST MEMORY ACCESS ADDRESS",        # 7  (memory diag)
    "LAST MEMORY READ ADDRESS",          # 8  (memory diag)
    "LAST MEMORY WRITE ADDRESS",         # 9  (memory diag)
    "LAST MEMORY ACCESS VALUE",          # 10 (memory diag)
    "LAST MEMORY ACCESS WIDTH",          # 11 (memory diag)
    "GENERAL PURPOSE REGISTERS",         # 12
    "EFLAGS",                            # 13
    "CPU MODE",                          # 14
    "LAST I/O PORT",                     # 15 (not emulated yet)
    "LAST INTERRUPT",                    # 16 (not emulated yet)
    "DISK ACTIVITY",                     # 17 (not emulated yet)
    "EMULATED RAM USAGE",                # 18
    "HOST MEMORY ESTIMATE",              # 19 (not measurable)
    "FRAME RATE",                        # 20
    "INSTRUCTION TRACE",                 # 21
]
MEM_DIAG = range(7, 12)


class Labels:
    """Constant text stored as ASCII codes in three lists (ROM-like)."""

    def __init__(self, p):
        self.codes = p.list("LABEL codes", [])
        self.start = p.list("LABEL start", [])
        self.length = p.list("LABEL length", [])
        self._ids = {}
        self._c, self._s, self._l = (p.lists["LABEL codes"][1], p.lists["LABEL start"][1], p.lists["LABEL length"][1])

    def id(self, text):
        if text not in self._ids:
            self._ids[text] = len(self._s) + 1
            self._s.append(len(self._c) + 1)
            self._l.append(len(text))
            self._c.extend(ord(ch) for ch in text)
        return self._ids[text]


def declare(p, S):
    v = p.var
    S.POWER = v("POWER", 1)
    S.PAUSED = v("PAUSED")
    S.MENU_OPEN = v("MENU_OPEN")
    S.SCREEN_ON = v("SCREEN_ON", 1)
    S.FRAME = v("frame counter")
    S.FPS = v("FPS")
    S.IPS = v("IPS")
    S.T_LAST = v("stats: last timer")
    S.N_LAST = v("stats: last insn count")
    S.F_LAST = v("stats: last frame")
    S.UI_ROW = v("ui cursor row")
    S.UI_COL = v("ui cursor col")
    S.UI_ATTR = v("ui attribute", ATTR_OV)
    S.UI_I = v("ui loop i")
    S.UI_T = v("ui temp")
    S.UI_N = v("ui digits")
    S.UI_S = v("ui scratch")
    S.CLICK_PENDING = v("click pending")
    S.CLICK_COL = v("click col")
    S.CLICK_ROW = v("click row")
    S.OV_ROW = v("overlay row")
    S.OV_J = v("overlay loop j")
    S.OV_K = v("overlay loop k")
    S.UI_HAS = v("ui layer non-empty")
    S.DIAG_ANY = v("diag: any overlay enabled")
    S.DIAG_ON = p.list("DIAG_ON (settings toggles)", [0] * len(DIAG_ITEMS))
    S.UI_BUF = p.list("UI_BUF (80x25 overlay layer)", [-1] * 2000)
    S.MENU_ITEM = v("menu item index")
    S.LBL = Labels(p)
    return S


def build(p, S, mem, P):
    L = S.LBL
    lid = L.id

    def new(name, args=()):
        pr = p.procedure(name, args)
        setattr(P, name, pr)
        return pr

    # ---- text-layer primitives (cursor based) -------------------------------------
    pr = new("ui_clear")
    with pr.body() as (b,):
        b.set(S.UI_I, 1)
        with b.while_not(S.UI_I > 2000) as w:
            w.replace(S.UI_BUF, S.UI_I, -1)
            w.change(S.UI_I, 1)

    pr = new("ui_at", ["row", "col"])
    with pr.body() as (b, row, col):
        b.set(S.UI_ROW, row)
        b.set(S.UI_COL, col)

    pr = new("ui_chr", ["code"])
    with pr.body() as (b, code):
        with b.if_(AND(S.UI_COL < 80, S.UI_ROW < 25)) as t:
            t.replace(S.UI_BUF, S.UI_ROW * 80 + S.UI_COL + 1, code + 256 * S.UI_ATTR)
        b.change(S.UI_COL, 1)

    pr = new("ui_label", ["id"])
    with pr.body() as (b, lbl):
        b.set(S.UI_I, 0)
        with b.while_not(ge(S.UI_I, L.length[lbl])) as w:
            w.call(P.ui_chr, L.codes[L.start[lbl] + S.UI_I])
            w.change(S.UI_I, 1)

    pr = new("ui_hex", ["val", "digits"])
    with pr.body() as (b, val, digits):
        b.set(S.UI_T, val)
        b.set(S.UI_I, digits - 1)
        with b.while_not(S.UI_I < 0) as w:
            d = S.UI_T % 16
            with w.if_(S.UI_ROW < 25) as t:
                t.replace(S.UI_BUF, S.UI_ROW * 80 + S.UI_COL + S.UI_I + 1,
                          48 + d + floor(d / 10) * 7 + 256 * S.UI_ATTR)
            w.set(S.UI_T, floor(S.UI_T / 16))
            w.change(S.UI_I, -1)
        b.change(S.UI_COL, digits)

    pr = new("ui_dec", ["val"])
    with pr.body() as (b, val):
        b.set(S.UI_T, val)
        b.set(S.UI_N, 1)
        b.set(S.UI_I, floor(val / 10))
        with b.while_not(S.UI_I < 1) as w:
            w.change(S.UI_N, 1)
            w.set(S.UI_I, floor(S.UI_I / 10))
        b.set(S.UI_I, S.UI_N - 1)
        with b.while_not(S.UI_I < 0) as w:
            with w.if_(S.UI_ROW < 25) as t:
                t.replace(S.UI_BUF, S.UI_ROW * 80 + S.UI_COL + S.UI_I + 1, 48 + S.UI_T % 10 + 256 * S.UI_ATTR)
            w.set(S.UI_T, floor(S.UI_T / 10))
            w.change(S.UI_I, -1)
        b.change(S.UI_COL, S.UI_N)

    def text(b, s):
        b.call(P.ui_label, lid(s))

    # ---- diagnostics ----------------------------------------------------------------
    pr = new("diag_update")
    with pr.body() as (b,):
        b.set(S.UI_S, 0)                     # UI_S = enabled memory-diag items
        for k in MEM_DIAG:
            b.set(S.UI_S, S.UI_S + S.DIAG_ON[k])
        b.set(S.DIAG_MEM, floor((S.UI_S + 4) / 5))
        b.set(S.DIAG_TRACE, S.DIAG_ON[21])
        b.set(S.UI_S, 0)
        for k in range(1, 22):
            b.set(S.UI_S, S.UI_S + S.DIAG_ON[k])
        b.set(S.DIAG_ANY, floor((S.UI_S + 20) / 21))

    def flag_char(b, flag, letter):
        with b.if_(eq(flag, 1)) as t:
            t.call(P.ui_chr, ord(letter))
        with b.else_() as t:
            t.call(P.ui_chr, ord("."))

    pr = new("ui_build_overlay")
    with pr.body() as (b,):
        b.set(S.UI_ATTR, ATTR_OV)
        b.set(S.OV_ROW, 0)

        def item(k):
            return b.if_(eq(S.DIAG_ON[k], 1))

        def begin(t):
            t.call(P.ui_at, S.OV_ROW, 0)

        def end(t, rows=1):
            # pad with spaces so the overlay line is readable over guest text
            with t.while_not(ge(S.UI_COL, 46)) as w:
                w.call(P.ui_chr, 32)
            t.change(S.OV_ROW, rows)

        with item(1) as t:
            begin(t); text(t, "IPS: "); t.call(P.ui_dec, S.IPS); end(t)
        with item(2) as t:
            begin(t); text(t, "INSNS: "); t.call(P.ui_dec, S.INSN_COUNT); end(t)
        with item(3) as t:
            begin(t); text(t, "CS:EIP: "); t.call(P.ui_hex, S.SEG[2], 4); text(t, ":"); t.call(P.ui_hex, S.EIP, 8); end(t)
        with item(4) as t:
            begin(t); text(t, "FETCH: "); t.call(P.ui_hex, S.LAST_FETCH, 8); end(t)
        with item(5) as t:
            begin(t); text(t, "BYTES: ")
            t.set(S.OV_J, 0)
            with t.while_not(S.OV_J > 5) as w:
                w.call(mem.mem_fb, S.LAST_FETCH + S.OV_J)
                w.call(P.ui_hex, S.MEMV, 2)
                w.call(P.ui_chr, 32)
                w.change(S.OV_J, 1)
            end(t)
        with item(6) as t:
            begin(t); text(t, "LAST OPCODE: "); t.call(P.ui_hex, S.LAST_OPCODE, 2); end(t)
        with item(7) as t:
            begin(t); text(t, "MEM ")
            with t.if_(eq(S.LAST_MEM_RW, 0)) as u:
                text(u, "NONE")
            with t.if_(eq(S.LAST_MEM_RW, 1)) as u:
                text(u, "READ  PHYS "); u.call(P.ui_hex, S.LAST_RD_PHYS, 8)
            with t.if_(eq(S.LAST_MEM_RW, 2)) as u:
                text(u, "WRITE PHYS "); u.call(P.ui_hex, S.LAST_WR_PHYS, 8)
            end(t)
        with item(8) as t:
            begin(t); text(t, "MEM RD LIN "); t.call(P.ui_hex, S.LAST_RD_LIN, 8)
            text(t, " PHYS "); t.call(P.ui_hex, S.LAST_RD_PHYS, 8); end(t)
        with item(9) as t:
            begin(t); text(t, "MEM WR LIN "); t.call(P.ui_hex, S.LAST_WR_LIN, 8)
            text(t, " PHYS "); t.call(P.ui_hex, S.LAST_WR_PHYS, 8); end(t)
        with item(10) as t:
            begin(t); text(t, "MEM VALUE: "); t.call(P.ui_hex, S.LAST_MEM_VAL, 8); end(t)
        with item(11) as t:
            begin(t); text(t, "MEM WIDTH: "); t.call(P.ui_dec, S.LAST_MEM_W); text(t, " BYTES"); end(t)
        with item(12) as t:
            for r, names in enumerate((("EAX", "ECX", "EDX", "EBX"), ("ESP", "EBP", "ESI", "EDI"))):
                begin(t)
                for i, nm in enumerate(names):
                    text(t, nm + " ")
                    t.call(P.ui_hex, S.GPR[r * 4 + i + 1], 8)
                    text(t, " ")
                end(t)
        with item(13) as t:
            begin(t); text(t, "EFLAGS: ")
            t.call(P.flags_pack)
            t.call(P.ui_hex, S.EFL, 8); text(t, " ")
            for flag, letter in ((S.OF, "O"), (S.DF, "D"), (S.IF, "I"), (S.TF, "T"), (S.SF, "S"),
                                 (S.ZF, "Z"), (S.AF, "A"), (S.PF, "P"), (S.CF, "C")):
                flag_char(t, flag, letter)
            end(t)
        with item(14) as t:
            begin(t); text(t, "MODE: REAL (16-BIT)  STATE: ")
            with t.if_(eq(S.CPU_STATE, 0)) as u:
                text(u, "OFF")
            with t.if_(eq(S.CPU_STATE, 1)) as u:
                text(u, "RUNNING")
            with t.if_(eq(S.CPU_STATE, 2)) as u:
                text(u, "HALTED")
            with t.if_(eq(S.CPU_STATE, 3)) as u:
                text(u, "FAULT")
            end(t)
        with item(15) as t:
            begin(t); text(t, "I/O PORT: N/A (NO I/O YET)"); end(t)
        with item(16) as t:
            begin(t); text(t, "INTERRUPT: N/A (NO INT YET)"); end(t)
        with item(17) as t:
            begin(t); text(t, "DISK: N/A (NO DISKS YET)"); end(t)
        with item(18) as t:
            begin(t); text(t, "RAM: ")
            t.call(P.ui_dec, S.RAM_BANKS * 64); text(t, " KIB IN ")
            t.call(P.ui_dec, S.RAM_BANKS); text(t, " BANKS"); end(t)
        with item(19) as t:
            begin(t); text(t, "HOST MEM: N/A (NOT MEASURABLE)"); end(t)
        with item(20) as t:
            begin(t); text(t, "FPS: "); t.call(P.ui_dec, S.FPS); end(t)
        with item(21) as t:
            t.set(S.OV_J, 0)
            with t.while_not(S.OV_J > 7) as w:
                w.call(P.ui_at, S.OV_ROW, 0)
                w.set(S.OV_K, (S.TRACE_POS + S.OV_J) % 8 + 1)
                w.call(P.ui_hex, S.TRACE_CS[S.OV_K], 4)
                text(w, ":")
                w.call(P.ui_hex, S.TRACE_IP[S.OV_K], 8)
                text(w, " OP ")
                w.call(P.ui_hex, S.TRACE_OP[S.OV_K], 2)
                with w.while_not(ge(S.UI_COL, 46)) as x:
                    x.call(P.ui_chr, 32)
                w.change(S.OV_ROW, 1)
                w.change(S.OV_J, 1)

    # ---- fault banner (always shown on a CPU fault, independent of diag settings) ---------
    pr = new("ui_build_fault")
    with pr.body() as (b,):
        b.set(S.UI_ATTR, ATTR_ERR)
        b.call(P.ui_at, 24, 0)
        text(b, "CPU FAULT #UD: UNSUPPORTED OPCODE ")
        b.call(P.ui_hex, S.FAULT_OPC, 2)
        text(b, " AT ")
        b.call(P.ui_hex, S.SEG[2], 4)
        text(b, ":")
        b.call(P.ui_hex, S.FAULT_EIP, 8)
        text(b, " ")

    # ---- settings menu -----------------------------------------------------------------
    actions = ["POWER", "RESET", "PAUSE", "STEP", "CLOSE"]
    entries = [("act", i) for i in range(len(actions))] + [("diag", k) for k in range(1, 22)]
    per_col = 13
    S.MENU_LAYOUT = []   # (entry, row, col)
    for n, e in enumerate(entries):
        S.MENU_LAYOUT.append((e, 2 + n % per_col, 0 if n < per_col else 40))

    pr = new("ui_build_menu")
    with pr.body() as (b,):
        b.call(P.ui_clear)
        b.set(S.UI_ATTR, ATTR_TITLE)
        b.call(P.ui_at, 0, 0)
        text(b, " SCRATCH86 V0.1 SETTINGS" + " " * 55)
        b.set(S.UI_ATTR, ATTR_MENU)
        b.call(P.ui_at, 1, 0)
        text(b, " CONTROLS                               DIAGNOSTIC OVERLAYS (DEFAULT OFF)")
        for (kind, idx), row, col in S.MENU_LAYOUT:
            b.call(P.ui_at, row, col)
            if kind == "act":
                b.set(S.UI_ATTR, ATTR_MENU)
                if idx == 0:
                    with b.if_(eq(S.POWER, 1)) as t:
                        text(t, "> POWER OFF")
                    with b.else_() as t:
                        text(t, "> POWER ON")
                elif idx == 2:
                    with b.if_(eq(S.PAUSED, 1)) as t:
                        text(t, "> RESUME")
                    with b.else_() as t:
                        text(t, "> PAUSE")
                else:
                    text(b, {1: "> RESET", 3: "> STEP ONE INSTRUCTION", 4: "> CLOSE SETTINGS"}[idx])
            else:
                with b.if_(eq(S.DIAG_ON[idx], 1)) as t:
                    t.set(S.UI_ATTR, ATTR_ON)
                    text(t, "[X] " + DIAG_ITEMS[idx - 1][:36])
                with b.else_() as t:
                    t.set(S.UI_ATTR, ATTR_MENU)
                    text(t, "[ ] " + DIAG_ITEMS[idx - 1][:36])
        b.set(S.UI_ATTR, ATTR_MENU)
        b.call(P.ui_at, 24, 0)
        text(b, " CLICK AN ITEM. CPU STATE IS ONLY CHANGED BETWEEN INSTRUCTIONS.")

    # ---- mouse click on the stage (menu interaction) -----------------------------------
    pr = new("ui_click", ["col", "row"])
    with pr.body() as (b, col, row):
        for (kind, idx), r, c in S.MENU_LAYOUT:
            with b.if_(AND(eq(row, r), eq(floor(col / 40) * 40, c))) as t:
                if kind == "act":
                    if idx == 0:
                        t.set(S.REQ, 1)
                    elif idx == 1:
                        t.set(S.REQ, 2)
                    elif idx == 2:
                        t.set(S.REQ, 4)
                    elif idx == 3:
                        t.set(S.REQ, 3)
                    else:
                        t.set(S.MENU_OPEN, 0)
                        t.call(P.ui_clear)
                else:
                    t.replace(S.DIAG_ON, idx, 1 - S.DIAG_ON[idx])
                    t.call(P.diag_update)
        with b.if_(eq(S.MENU_OPEN, 1)) as t:
            t.call(P.ui_build_menu)

    # The click hat only records the cell; ui_frame (CPU thread) processes it.
    with p.stage.on_click() as b:
        with b.if_(eq(S.MENU_OPEN, 1)) as t:
            t.set(S.CLICK_COL, floor((reporter("sensing_mousex") + 240) / 6))
            t.set(S.CLICK_ROW, floor((175 - reporter("sensing_mousey")) / 14))
            t.set(S.CLICK_PENDING, 1)

    # ---- per-frame stats + UI layer refresh ---------------------------------------------
    pr = new("ui_frame")
    with pr.body() as (b,):
        b.change(S.FRAME, 1)
        with b.if_(eq(S.CLICK_PENDING, 1)) as t:
            t.set(S.CLICK_PENDING, 0)
            t.call(P.ui_click, S.CLICK_COL, S.CLICK_ROW)
        timer = reporter("sensing_timer")
        with b.if_(ge(timer - S.T_LAST, 1)) as t:
            t.set(S.UI_S, timer - S.T_LAST)
            t.set(S.IPS, round_((S.INSN_COUNT - S.N_LAST) / S.UI_S))
            with t.if_(S.IPS < 0) as u:
                u.set(S.IPS, 0)
            t.set(S.FPS, round_((S.FRAME - S.F_LAST) / S.UI_S))
            t.set(S.T_LAST, timer)
            t.set(S.N_LAST, S.INSN_COUNT)
            t.set(S.F_LAST, S.FRAME)
        with b.if_(eq(S.MENU_OPEN, 1)) as t:
            with t.if_(eq(S.FRAME % 4, 0)) as u:
                u.call(P.ui_build_menu)
            t.set(S.UI_HAS, 1)
        with b.else_() as t:
            with t.if_(OR(eq(S.DIAG_ANY, 1), eq(S.CPU_STATE, 3))) as u:
                with u.if_(eq(S.FRAME % 4, 0)) as w:
                    w.call(P.ui_clear)
                    w.call(P.ui_build_overlay)
                    with w.if_(eq(S.CPU_STATE, 3)) as x:
                        x.call(P.ui_build_fault)
                u.set(S.UI_HAS, 1)
            with t.else_() as u:
                with u.if_(eq(S.UI_HAS, 1)) as w:
                    w.call(P.ui_clear)
                    w.set(S.UI_HAS, 0)

    return P
