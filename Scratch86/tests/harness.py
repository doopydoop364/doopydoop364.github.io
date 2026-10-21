"""Python side of the Scratch-runtime test harness.

Builds the project (cached per session), writes cases to JSON, runs them in the
stock scratch-vm via tools/vmrunner/run_cases.cjs and returns the results.
All guest execution happens inside the Scratch blocks; Python only prepares
inputs and compares outputs.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src", "scratch"))
sys.path.insert(0, os.path.join(ROOT, "tools", "assembler"))
from asm import assemble  # noqa: E402

_SB3 = None


def project_path():
    global _SB3
    if _SB3 is None:
        d = tempfile.mkdtemp(prefix="s86test_")
        _SB3 = os.path.join(d, "Scratch86-test.sb3")
        subprocess.run([sys.executable, os.path.join(ROOT, "src", "scratch", "build.py"), "--out", _SB3],
                       check=True, capture_output=True)
    return _SB3


def run_cases(cases, sb3=None):
    sb3 = sb3 or project_path()
    with tempfile.TemporaryDirectory() as d:
        cin, cout = os.path.join(d, "in.json"), os.path.join(d, "out.json")
        with open(cin, "w") as f:
            json.dump(cases, f)
        r = subprocess.run(["node", os.path.join(ROOT, "tools", "vmrunner", "run_cases.cjs"), sb3, cin, cout],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode != 0:
            raise RuntimeError("vm runner failed:\n" + r.stdout + r.stderr)
        with open(cout) as f:
            res = json.load(f)
    for x in res:
        if "error" in x:
            raise RuntimeError("case %s: %s" % (x["name"], x["error"]))
    return res


def run_asm(src, **kw):
    """Assemble `src`, run it from 0000:7C00 and return the single result."""
    case = {"name": "asm", "code": list(assemble(src))}
    case.update(kw)
    return run_cases([case])[0]
