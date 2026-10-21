"""Machine scheduler: the main CPU loop, power/reset/pause/step requests and
the green-flag entry point.

Everything that changes CPU state happens here, between instructions.  UI
scripts only set `REQ`.  See ui.py for the request codes.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "sb3_generator"))
from sb3gen import *  # noqa: E402,F401,F403


def build(p, S, mem, P):
    def new(name, args=()):
        pr = p.procedure(name, args)
        setattr(P, name, pr)
        return pr

    pr = new("mem_clear")           # power-cycle: DRAM contents are lost
    with pr.body() as (b,):
        for lst in S.RAM:
            b.delete_all(lst)
            with b.repeat(65536) as w:
                w.add(lst, 0)

    pr = new("handle_req")
    with pr.body() as (b,):
        b.set(S.TB, S.REQ)         # (CPU temps are idle between instructions)
        b.set(S.REQ, 0)
        with b.if_(eq(S.TB, 1)) as t:          # power toggle
            with t.if_(eq(S.POWER, 1)) as u:
                u.set(S.POWER, 0)
                u.set(S.CPU_STATE, 0)
                u.set(S.SCREEN_ON, 0)
            with t.else_() as u:
                u.set(S.POWER, 1)
                u.call(P.mem_clear)
                u.call(P.machine_boot_test)
                u.set(S.PAUSED, 0)
                u.set(S.SCREEN_ON, 1)
        with b.if_(eq(S.TB, 2)) as t:          # reset (RAM keeps its contents)
            with t.if_(eq(S.POWER, 1)) as u:
                u.call(P.machine_boot_test)
                u.set(S.PAUSED, 0)
        with b.if_(eq(S.TB, 3)) as t:          # step one instruction (pauses the machine)
            with t.if_(eq(S.POWER, 1)) as u:
                u.set(S.PAUSED, 1)
                with u.if_(eq(S.CPU_STATE, 1)) as w:
                    w.call(P.cpu_run_batch, 1)
        with b.if_(eq(S.TB, 4)) as t:          # pause / resume
            t.set(S.PAUSED, 1 - S.PAUSED)

    with p.on_flag() as b:
        b.set(S.POWER, 1)
        b.set(S.PAUSED, 0)
        b.set(S.MENU_OPEN, 0)
        b.set(S.SCREEN_ON, 1)
        b.set(S.REQ, 0)
        b.set(S.CLICK_PENDING, 0)
        b.set(S.UI_HAS, 0)
        b.set(S.T_LAST, 0)
        b.set(S.N_LAST, 0)
        b.set(S.F_LAST, 0)
        b.set(S.FRAME, 0)
        b.raw("sensing_resettimer")
        b.call(P.diag_update)
        b.call(P.mem_clear)
        b.call(P.machine_boot_test)
        with b.forever() as w:
            with w.if_(ne(S.REQ, 0)) as t:
                t.call(P.handle_req)
            with w.if_(AND(eq(S.POWER, 1), eq(S.PAUSED, 0), eq(S.CPU_STATE, 1))) as t:
                t.call(P.cpu_run_batch, S.BUDGET)
            w.call(P.ui_frame)
    return P
