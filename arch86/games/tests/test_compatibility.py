import no_probe  # noqa: F401  (must come first: disables real system probes)
import unittest
from arch_games.compatibility.analyzer import analyze
from arch_games.compatibility.executables import inspect_bytes
from arch_games.models import Game
from test_detection import elf, pe


def profile(arch="x86_64"):
    return {
        "os": "linux",
        "os_architecture": arch,
        "ram_total": 4 * 1024**3,
        "ram_available": 3 * 1024**3,
        "graphics": {"display": True, "accelerated": False, "vulkan": False},
        "wine": {"available": True, "wow64": True, "win32": None, "win64": True, "version": "fixture"},
        "runtimes": {"dosbox": "/fixture/dosbox"},
    }


class CompatibilityTests(unittest.TestCase):
    def test_simple_windows(self):
        for bits in [32, 64]:
            result = analyze(
                Game("1", "Simple", "gamevault"), profile(), inspect_bytes(pe(bits)), {"graphics": "2d"}
            )
            self.assertEqual(result.status, "Promising")
            self.assertIn("Wine", result.runtime)

    def test_native_dos(self):
        for data in [elf(64, 62), b"MZ" + bytes(62)]:
            result = analyze(
                Game("1", "Simple", "gamevault"), profile(), inspect_bytes(data), {"graphics": "2d"}
            )
            self.assertEqual(result.status, "Promising")

    def test_blockers(self):
        p = profile("i686")
        result = analyze(Game("1", "Test"), p, inspect_bytes(pe(64)))
        self.assertEqual(result.status, "Unsupported")
        p = profile()
        p["wine"]["available"] = False
        self.assertEqual(analyze(Game("1", "Test"), p, inspect_bytes(pe())).status, "Unsupported")
        self.assertEqual(
            analyze(Game("1", "Test"), profile(), inspect_bytes(elf(64, 183))).status, "Unsupported"
        )

    def test_performance(self):
        for requirements in [{"acceleration": True}, {"ram_bytes": 8 * 1024**3}]:
            result = analyze(Game("1", "Test"), profile(), inspect_bytes(pe()), requirements)
            self.assertEqual(result.status, "Unlikely")

    def test_unknown_wow64_and_no_false_verification(self):
        p = profile()
        p["wine"]["wow64"] = None
        self.assertEqual(
            analyze(Game("1", "Test"), p, inspect_bytes(pe()), {"graphics": "2d"}).status, "Experimental"
        )
        game = Game("1", "Test", "gamevault", os="windows")
        self.assertEqual(analyze(game, p).status, "Unknown")
        result = analyze(
            game, p, inspect_bytes(pe()), feedback={"result": "works", "fingerprint": "other-machine"}
        )
        self.assertNotEqual(result.status, "Verified Working")
