import no_probe  # noqa: F401
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from arch_games.compatibility import graphics
from arch_games.compatibility.graphics import detect_graphics
from arch_games.catalog.library import Library
from arch_games.config import Config
from arch_games.storage.database import Database
from arch_games.platform import system_profile


class Recorder:
    """A fake runner that records every call (argv and env) and answers per tool."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs.get("env")))
        answer = self.answers.get(args[0], SimpleNamespace(returncode=0, stdout=""))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def tools(self):
        return [c[0][0] for c in self.calls]


VK = SimpleNamespace(returncode=0, stdout="GPU0:\n  deviceName = NVIDIA GeForce\n")


class GraphicsProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ARCH_GAMES_NO_PROBE", None)  # these tests drive the real code path with a recording runner
        self.addCleanup(os.environ.__setitem__, "ARCH_GAMES_NO_PROBE", "1")
        graphics.clear_cache()
        self.addCleanup(graphics.clear_cache)
        # route the "real" runner to the recorder
        self.rec = Recorder({"vulkaninfo": VK})
        real = mock.patch.object(subprocess, "run", self.rec)
        real.start()
        self.addCleanup(real.stop)

    def test_kill_switch_runs_nothing(self):
        os.environ["ARCH_GAMES_NO_PROBE"] = "1"
        result = detect_graphics(None, {"DISPLAY": ":0"})
        self.assertEqual(self.rec.calls, [])
        self.assertIsNone(result["vulkan"])
        self.assertTrue(result["evidence"])

    def test_vulkaninfo_never_sees_a_display(self):
        environ = {"DISPLAY": ":9", "WAYLAND_DISPLAY": "wayland-9", "HOME": "/h", "XAUTHORITY": "/nonexistent"}
        detect_graphics(None, environ)
        vulkan = [env for args, env in self.rec.calls if args[0] == "vulkaninfo"]
        self.assertEqual(len(vulkan), 1)
        self.assertNotIn("DISPLAY", vulkan[0])
        self.assertNotIn("WAYLAND_DISPLAY", vulkan[0])
        self.assertEqual(vulkan[0].get("HOME"), "/h")

    def test_glxinfo_needs_a_reachable_display(self):
        detect_graphics(None, {"DISPLAY": ":9", "XAUTHORITY": "/nonexistent/Xauthority"})
        self.assertNotIn("glxinfo", self.rec.tools())
        graphics.clear_cache()
        detect_graphics(None, {"DISPLAY": ":987654"})  # no such X socket
        self.assertNotIn("glxinfo", self.rec.tools())
        graphics.clear_cache()
        with mock.patch.object(graphics, "display_reachable", return_value=True):
            detect_graphics(None, {"DISPLAY": ":0"})
        self.assertIn("glxinfo", self.rec.tools())

    def test_display_reachable_checks_socket_and_xauthority(self):
        runtime = Path(self.temp.name)
        (runtime / "wayland-0").touch()
        self.assertTrue(graphics.display_reachable({"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(runtime)}))
        self.assertFalse(graphics.display_reachable({"WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": str(runtime)}))
        self.assertFalse(graphics.display_reachable({"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(runtime), "XAUTHORITY": str(runtime / "missing")}))
        self.assertFalse(graphics.display_reachable({}))

    def test_process_cache_probes_once(self):
        first = detect_graphics(None, {})
        count = len(self.rec.calls)
        self.assertGreater(count, 0)
        second = detect_graphics(None, {})
        self.assertEqual(len(self.rec.calls), count)
        self.assertEqual(first, second)
        self.assertNotIn("_statuses", first)

    def test_disk_cache_survives_a_new_process(self):
        detect_graphics(None, {})
        count = len(self.rec.calls)
        graphics._memory.clear()  # a fresh process only has the disk
        detect_graphics(None, {})
        self.assertEqual(len(self.rec.calls), count)

    def test_cache_expires(self):
        detect_graphics(None, {})
        count = len(self.rec.calls)
        later = graphics.time.time() + graphics.TTL + 5
        with mock.patch.object(graphics.time, "time", return_value=later):
            detect_graphics(None, {})
        self.assertGreater(len(self.rec.calls), count)

    def test_display_change_is_a_new_measurement(self):
        detect_graphics(None, {})
        count = len(self.rec.calls)
        detect_graphics(None, {"DISPLAY": ":1"})
        self.assertGreater(len(self.rec.calls), count)

    def test_signal_exit_is_a_crash_and_is_remembered(self):
        for code in (-11, 139):
            graphics.clear_cache()
            self.rec.calls.clear()
            self.rec.answers["vulkaninfo"] = SimpleNamespace(returncode=code, stdout="partial garbage deviceName")
            result = detect_graphics(None, {})
            self.assertIsNone(result["vulkan"])
            self.assertTrue(any("vulkan: probe crashed" in u for u in result["uncertainties"]))
            self.assertEqual(self.rec.tools().count("vulkaninfo"), 1)
            # even after the result cache expires, the crashing tool is left alone for the crash window
            graphics._memory.clear()
            later = graphics.time.time() + graphics.TTL + 5
            with mock.patch.object(graphics.time, "time", return_value=later):
                again = detect_graphics(None, {})
            self.assertEqual(self.rec.tools().count("vulkaninfo"), 1, code)
            self.assertTrue(any("probe crashed" in u for u in again["uncertainties"]))
            # ... and is tried again after 24 hours
            graphics._memory.clear()
            much_later = graphics.time.time() + graphics.CRASH_TTL + 5
            with mock.patch.object(graphics.time, "time", return_value=much_later):
                detect_graphics(None, {})
            self.assertEqual(self.rec.tools().count("vulkaninfo"), 2, code)

    def test_injected_runner_tolerates_signal_exit_and_is_not_cached(self):
        rec = Recorder({"vulkaninfo": SimpleNamespace(returncode=-11, stdout="")})
        first = detect_graphics(rec, {"DISPLAY": ":0"})
        self.assertIsNone(first["vulkan"])
        count = len(rec.calls)
        detect_graphics(rec, {"DISPLAY": ":0"})
        self.assertEqual(len(rec.calls), 2 * count)  # fakes are measured every time, so tests stay isolated
        self.assertTrue(all("DISPLAY" not in (env or {}) for args, env in rec.calls if args[0] == "vulkaninfo"))

    def test_timeout_and_missing_tools(self):
        rec = Recorder({"vulkaninfo": subprocess.TimeoutExpired("vulkaninfo", 5), "lspci": FileNotFoundError()})
        result = detect_graphics(rec, {})
        self.assertIsNone(result["vulkan"])
        self.assertEqual(result["adapters"], [])
        self.assertTrue(any("timed out" in u for u in result["uncertainties"]))

    def graphics_calls(self):
        return [c for c in self.rec.calls if c[0][0] in {"vulkaninfo", "lspci", "glxinfo"}]

    def test_two_library_loads_probe_once(self):
        with tempfile.TemporaryDirectory() as state:
            config = Config(Path(state))
            db = Database(config.data_dir / "state.db")
            Library(config, db).load(offline=True)
            count = len(self.graphics_calls())
            self.assertGreater(count, 0)
            Library(config, db).load(offline=True)
            self.assertEqual(len(self.graphics_calls()), count)

    def test_system_profile_default_runner_is_cached(self):
        system_profile(self.temp.name, environ={})
        count = len(self.graphics_calls())
        self.assertGreater(count, 0)
        system_profile(self.temp.name, environ={})
        self.assertEqual(len(self.graphics_calls()), count)


class SuiteGuardTests(unittest.TestCase):
    def test_every_test_module_disables_probing(self):
        for path in sorted(Path(__file__).parent.glob("test_*.py")):
            self.assertIn("import no_probe", path.read_text(), path.name)
        self.assertEqual(os.environ.get("ARCH_GAMES_NO_PROBE"), "1")


if __name__ == "__main__":
    unittest.main()
