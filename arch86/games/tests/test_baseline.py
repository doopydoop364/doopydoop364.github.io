import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from arch_games.catalog.pacman import load_catalog, parse_catalog, visible
from arch_games.platform import architecture


class BaselineTests(unittest.TestCase):
    def test_architectures(self):
        for value in ["pentium4", "i686", "i386"]:
            self.assertEqual(architecture(value), "i686")
        self.assertEqual(architecture("amd64"), "x86_64")
        self.assertEqual(architecture("aarch64"), "aarch64")

    def test_catalog(self):
        self.assertEqual(len(load_catalog()), 69)
        self.assertEqual(parse_catalog("bad\nName|--bad|A|x|B"), [])

    def test_availability_includes_installed(self):
        def run(args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="openttd\n" if args[-1] == "-Slq" else "nethack\n")

        games, error = visible(run=run)
        self.assertFalse(error)
        self.assertEqual({g.package for g in games}, {"openttd", "nethack"})

    def test_cli(self):
        root = Path(__file__).parents[1]
        for cmd in [["-m", "arch_games", "--help"], ["arch86-games.py", "--version"]]:
            result = subprocess.run([sys.executable] + cmd, cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_module_imports_and_catalog_copy(self):
        import arch_games
        import importlib
        import pkgutil

        for entry in pkgutil.walk_packages(arch_games.__path__, "arch_games."):
            if not entry.name.endswith(".__main__"):
                importlib.import_module(entry.name)
        root = Path(__file__).parents[1]
        self.assertEqual((root / "catalog.txt").read_bytes(), (root / "arch_games/catalog.txt").read_bytes())
