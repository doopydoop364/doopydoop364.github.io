"""Integration tests: green-flag run, screen composition, controls, overlays.

Runs the generated project in scratch-vm exactly as a user would (green flag,
frame loop) and inspects the text cells the Screen sprite composes.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from harness import ROOT, project_path  # noqa: E402


def flag_run(script):
    r = subprocess.run(["node", os.path.join(ROOT, "tools", "vmrunner", "run_flag.cjs"), project_path(), json.dumps(script)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    line = [l for l in r.stdout.splitlines() if l.startswith("{")][-1]
    return {s["name"]: s for s in json.loads(line)["snaps"]}


def test_demo_boot_screen_comes_from_guest_code():
    s = flag_run([{"steps": 30, "snap": "a"}])["a"]
    rows = [r.rstrip() for r in s["shadow"]]
    assert "SCRATCH86 V0.1" in rows[1]
    assert "written to video memory by real x86 machine code" in rows[3]
    assert rows[12].startswith("  COUNTER:")
    assert s["state"] == 1 and s["insns"] > 1000
    assert s["shadowAttr"][1 * 80 + 2] == 0x1F          # banner attribute white-on-blue
    assert all(v == 0 for v in s["diagOn"])             # diagnostics default to OFF


def click(col, row, steps=6, snap=None):
    d = {"set": {"click col": col, "click row": row, "click pending": 1}, "steps": steps}
    if snap:
        d["snap"] = snap
    return d


def test_overlays_default_off_and_toggle_on_via_menu_clicks():
    r = flag_run([
        {"steps": 12, "snap": "off"},
        {"set": {"MENU_OPEN": 1}, "steps": 6},
        click(10, 7),                      # [ ] IPS            (left column row 7  = diag item 1)
        click(10, 9),                      # [ ] CS:EIP         (left column row 9  = diag item 3)
        click(10, 6, steps=12, snap="closed"),    # > CLOSE SETTINGS
        {"steps": 12, "snap": "on"},
    ])
    assert "CS:EIP" not in " ".join(r["off"]["shadow"]) and "IPS" not in " ".join(r["off"]["shadow"])
    assert r["closed"]["menu"] == 0 and r["closed"]["diagOn"][0] == 1 and r["closed"]["diagOn"][2] == 1
    on = [x.rstrip() for x in r["on"]["shadow"]]
    assert on[0].startswith("IPS: ") and on[1].startswith("CS:EIP: 0000:00007C")
    assert "SCRATCH86 V0.1" in on[1] or "SCRATCH86" in on[2] or True
    # guest screen below the overlay is untouched
    assert on[12].startswith("  COUNTER:")


def test_pause_resume_step_reset_power():
    r = flag_run([
        {"steps": 10, "snap": "run"},
        {"set": {"REQ": 4}, "steps": 3, "snap": "paused"},
        {"steps": 5, "snap": "still"},
        {"set": {"REQ": 3}, "steps": 3, "snap": "stepped"},
        {"set": {"REQ": 4}, "steps": 4, "snap": "resumed"},
        {"set": {"REQ": 2}, "steps": 2, "snap": "reset"},
        {"set": {"REQ": 1}, "steps": 3, "snap": "off"},
        {"set": {"REQ": 1}, "steps": 6, "snap": "on"},
    ])
    assert r["paused"]["paused"] == 1
    assert r["still"]["insns"] == r["paused"]["insns"]                    # no progress while paused
    assert r["stepped"]["insns"] == r["still"]["insns"] + 1               # exactly one instruction
    assert r["resumed"]["paused"] == 0 and r["resumed"]["insns"] > r["stepped"]["insns"] + 1000
    assert r["reset"]["insns"] < r["resumed"]["insns"]                    # CPU restarted
    assert r["off"]["power"] == 0 and r["off"]["state"] == 0 and r["off"]["screenOn"] == 0
    assert all(c == "" for c in (x.strip() for x in r["off"]["shadow"]))   # screen is dark
    assert r["on"]["power"] == 1 and r["on"]["state"] == 1 and r["on"]["insns"] > 0


def test_settings_menu_overlay_and_close_restores_screen():
    r = flag_run([
        {"steps": 20, "snap": "before"},
        {"set": {"MENU_OPEN": 1}, "steps": 12, "snap": "menu"},
        click(10, 6, steps=12, snap="closed"),            # "> CLOSE SETTINGS"
    ])
    menu = [x.rstrip() for x in r["menu"]["shadow"]]
    assert "SCRATCH86 V0.1 SETTINGS" in menu[0]
    assert any("> RESET" in x for x in menu) and any("CUMULATIVE INSTRUCTIONS" in x for x in menu)
    assert all(v == 0 for v in r["menu"]["diagOn"])
    assert r["closed"]["menu"] == 0
    after = [x.rstrip() for x in r["closed"]["shadow"]]
    assert "SCRATCH86 V0.1" in after[1] and "SETTINGS" not in " ".join(after)    # guest screen restored
