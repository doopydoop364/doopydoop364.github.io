"""Inspect installed runtimes, without creating a prefix or installing anything."""
import shutil
import subprocess
from .graphics import command


def detect_wine(run=subprocess.run, which=shutil.which):
    wine = which("wine")
    return {"available": bool(wine), "path": wine or "", "version": command([wine, "--version"], run).strip() if wine else "",
            "win32": None, "win64": None, "wow64": None,
            "evidence": ["Wine command found" if wine else "Wine command missing"],
            "uncertainties": ["Wine version alone does not establish prefix architectures or WoW64 support"]}
