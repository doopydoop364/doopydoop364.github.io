"""Build-system tests: determinism, validity, and the 'pure Scratch' guard."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools", "validators"))
from validate_sb3 import validate  # noqa: E402

# Core Scratch block categories only.  Anything else (an extension block that
# could hide a JavaScript implementation) fails this test.
ALLOWED_PREFIXES = ("control_", "data_", "event_", "operator_", "procedures_", "argument_", "looks_",
                    "motion_", "sensing_", "pen_")
ALLOWED_EXTENSIONS = {"pen"}


def build(out):
    subprocess.run([sys.executable, os.path.join(ROOT, "src", "scratch", "build.py"), "--out", out],
                   check=True, capture_output=True)


def test_build_is_deterministic():
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.sb3"), os.path.join(d, "b.sb3")
        build(a)
        build(b)
        assert hashlib.sha256(open(a, "rb").read()).hexdigest() == hashlib.sha256(open(b, "rb").read()).hexdigest()


def test_project_validates_and_contains_scripts():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.sb3")
        build(p)
        errors, info = validate(p)
        assert errors == []
        assert info["scripts"] > 50 and info["blocks"] > 3000


def test_only_native_scratch_blocks_are_used():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.sb3")
        build(p)
        proj = json.loads(zipfile.ZipFile(p).read("project.json"))
        assert set(proj["extensions"]) <= ALLOWED_EXTENSIONS
        ops = {b["opcode"] for t in proj["targets"] for b in t["blocks"].values()}
        bad = sorted(o for o in ops if not o.startswith(ALLOWED_PREFIXES))
        assert bad == [], bad
        assert not any("javascript" in json.dumps(t).lower() for t in proj["targets"])
