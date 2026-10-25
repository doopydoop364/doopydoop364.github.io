from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from arch_games.catalog.library import Library
from arch_games.config import AppError, Config
from arch_games.core import history
from arch_games.performance import fps, service
from arch_games.performance.sampler import Sampler, read_stat, tree, xdotool_probe
from arch_games.storage.database import Database


def fake_proc(root, procs):
    """procs: {pid: (ppid, utime, stime, rss_pages)}"""
    for pid, (ppid, ut, st, pages) in procs.items():
        d = Path(root) / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        fields = ["S", str(ppid)] + ["0"] * 9 + [str(ut), str(st)] + ["0"] * 30
        (d / "stat").write_text("{} (wine (preloader)) {}\n".format(pid, " ".join(fields)))
        (d / "statm").write_text("1000 {} 10 0 0 0 0\n".format(pages))


class SamplerTests(unittest.TestCase):
    def test_tree_and_stat_parsing_with_odd_names(self):
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d, {10: (1, 5, 5, 100), 11: (10, 0, 0, 50), 12: (11, 0, 0, 10), 99: (1, 0, 0, 1)})
            self.assertEqual(sorted(tree(d, 10)), [10, 11, 12])
            self.assertEqual(read_stat(d, 10), (1, 10))
            self.assertIsNone(read_stat(d, 4242))
            self.assertEqual(tree(d, 4242), [])

    def test_cpu_and_memory_from_deltas(self):
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d, {10: (1, 0, 0, 100), 11: (10, 0, 0, 50)})
            now = [0.0]
            s = Sampler(10, 1.0, proc_root=d, clock=lambda: now[0], clk_tck=100, page=4096, cpus=4)
            self.assertTrue(s.sample())
            now[0] = 2.0
            fake_proc(d, {10: (1, 100, 0, 100), 11: (10, 50, 0, 60)})  # 150 ticks in 2 s at 100 Hz = 75% of a core
            s.sample()
            summary = s.summary()
            self.assertEqual(summary["cpu_percent_peak"], 75.0)
            self.assertEqual(summary["rss_bytes_peak"], 160 * 4096)
            self.assertEqual(summary["samples"], 2)
            self.assertIn("of one core", summary["cpu_percent_note"])

    def test_vanished_process_stops_sampling_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            s = Sampler(10, 1.0, proc_root=d, clk_tck=100, page=4096)
            self.assertFalse(s.sample())
            self.assertIsNone(s.summary()["cpu_percent_avg"])

    def test_finished_child_cpu_is_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d, {10: (1, 0, 0, 10), 11: (10, 100, 0, 10)})
            now = [0.0]
            s = Sampler(10, 1.0, proc_root=d, clock=lambda: now[0], clk_tck=100, page=4096)
            s.sample()
            import shutil
            shutil.rmtree(Path(d) / "11")
            now[0] = 1.0
            s.sample()
            self.assertEqual(s.samples[-1]["cpu_percent"], 0.0)  # no negative spike when a child exits

    def test_real_process_busy_loop_measured_on_this_kernel(self):
        child = subprocess.Popen([sys.executable, "-c", "import time\nend=time.time()+2\nwhile time.time()<end: pass"])
        try:
            sampler = Sampler(child.pid, 0.3).start()
            time.sleep(1.5)
            summary = sampler.stop()
        finally:
            child.kill()
            child.wait()
        self.assertGreater(summary["cpu_percent_peak"], 50)
        self.assertGreater(summary["rss_bytes_peak"], 1_000_000)

    def test_window_probe_unavailable_without_xdotool_and_never_fabricated(self):
        self.assertIsNone(xdotool_probe({"DISPLAY": ":0"}, which=lambda n: None))
        self.assertIsNone(xdotool_probe({}, which=lambda n: "/usr/bin/xdotool"))
        calls = []
        probe = xdotool_probe({"DISPLAY": ":0"}, which=lambda n: "/x", run=lambda *a, **k: (calls.append(a), subprocess.CompletedProcess(a, 0, "123\n", ""))[1])
        self.assertTrue(probe([5, 6]))
        with tempfile.TemporaryDirectory() as d:
            fake_proc(d, {10: (1, 0, 0, 1)})
            s = Sampler(10, 1.0, proc_root=d, clk_tck=100, page=4096, probe=lambda pids: True)
            s.sample()
            self.assertEqual(s.summary()["first_window_seconds"], s.first_window)
            self.assertIsNotNone(s.first_window)


class FpsTests(unittest.TestCase):
    def test_mechanism_selection_never_invents(self):
        self.assertEqual(fps.mechanism({}, which=lambda n: "/usr/bin/mangohud"), "mangohud")
        self.assertEqual(fps.mechanism({"graphics": {"renderer": "llvmpipe (LLVM 19)"}}, which=lambda n: None), "gallium-hud")
        self.assertIsNone(fps.mechanism({"graphics": {"renderer": "unknown"}}, which=lambda n: None))
        self.assertFalse(fps.UNAVAILABLE["available"])
        self.assertIn("not estimated", fps.UNAVAILABLE["reason"])

    def test_parse_mangohud_and_gallium(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "game_2026.csv").write_text("os,cpu,gpu\nlinux,x,y\nfps,frametime,cpu_load\n60,16.6,10\n30,33.3,20\nbad,row,1\n45,22,5\n")
            (Path(d) / "game_2026_summary.csv").write_text("fps,x\n9999,1\n")
            result = fps.summarize("mangohud", d)
            self.assertTrue(result["available"])
            self.assertEqual((result["samples"], result["fps_avg"], result["fps_min"], result["fps_max"]), (3, 45.0, 30.0, 60.0))
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "fps").write_text("59.9\n60.1\nnot-a-number\n")
            result = fps.summarize("gallium-hud", d)
            self.assertEqual(result["samples"], 2)
        with tempfile.TemporaryDirectory() as d:
            result = fps.summarize("mangohud", d)
            self.assertFalse(result["available"])
            self.assertIn("no frame data", result["reason"])
            self.assertFalse(fps.summarize("gallium-hud", d)["available"])

    def test_environment_per_mechanism(self):
        self.assertIn("MANGOHUD_CONFIG", fps.environment("mangohud", "/tmp/x"))
        self.assertEqual(fps.environment("gallium-hud", "/tmp/x")["GALLIUM_HUD_DUMP_DIR"], "/tmp/x")
        self.assertEqual(fps.environment(None, "/tmp/x"), {})


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "s")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)


def launch(db, ident, game, start, seconds, state="exited", code=0, purpose="game"):
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc) + timedelta(hours=start)
    db.put("launches", ident, {"id": ident, "game_id": game, "started_at": t0.isoformat(), "finished_at": (t0 + timedelta(seconds=seconds)).isoformat(), "state": state, "exit_code": code, "purpose": purpose})


class HistoryTests(Base):
    def test_playtime_rules(self):
        launch(self.db, "a", "g:1", 0, 3600)
        launch(self.db, "b", "g:1", 2, 1800)
        launch(self.db, "c", "g:1", 4, 3, code=0)  # too short to count
        launch(self.db, "d", "g:1", 5, 20, code=1)  # crash on start
        launch(self.db, "e", "g:1", 6, 600, state="stopped_after_test", code=None)  # supervised test, not play
        launch(self.db, "f", "g:1", 7, 900, purpose="installer")
        launch(self.db, "g", "g:1", 8, 20 * 3600)  # absurd: capped
        launch(self.db, "h", "g:2", 1, 120)
        row = history.playtime(self.db, "g:1")["g:1"]
        self.assertEqual(row["counted_sessions"], 3)
        self.assertEqual(row["seconds"], 3600 + 1800 + 12 * 3600)
        self.assertEqual(row["sessions"], 6)  # launches of the game; installer runs are excluded
        self.assertIn("Estimate", row["note"])
        self.assertEqual(set(history.playtime(self.db)), {"g:1", "g:2"})
        self.assertTrue(row["last_played"].endswith("+00:00"))  # explicit timezone semantics

    def test_recent_order_and_naive_timestamps_treated_as_utc(self):
        launch(self.db, "a", "g:1", 0, 100)
        launch(self.db, "b", "g:2", 1, 100)
        launch(self.db, "c", "g:1", 2, 100)
        self.assertEqual([s["game_id"] for s in history.recent(self.db)], ["g:1", "g:2"])
        self.assertEqual(history.parse("2026-01-01T00:00:00").tzinfo, timezone.utc)
        self.assertIsNone(history.parse("garbage"))

    def test_custom_title_applies_in_library(self):
        from arch_games.models import Game

        self.db.put("installs", "local:x", {"directory": "d", "game": Game("local:x", "Original", "local").to_dict(), "archive_sha256": "a"})
        self.db.put("custom_titles", "local:x", {"title": "My Name"})
        lib = Library(self.config, self.db, {}).load(offline=True)
        game = next(g for g in lib.games if g.id == "local:x")
        self.assertEqual((game.title, game.metadata["original_title"]), ("My Name", "Original"))
        from arch_games.identity.resolver import Resolver, collect_candidates

        self.assertEqual(Resolver(collect_candidates(lib.games, self.db), self.db).resolve("my name")[0]["id"], "local:x")


class BenchmarkTests(Base):
    class FakeLauncher:
        def __init__(self):
            self.calls = []

        def plan_registered(self, game_id, extra_arguments=(), extra_env=None):
            self.calls.append(("plan", dict(extra_env or {})))
            return {"game_id": game_id}

        def launch(self, plan, timeout=None, monitor=None):
            sampler = monitor(os.getpid())  # real /proc sampling of this test process
            time.sleep(1.2)
            self.calls.append(("launch", timeout))
            return {"id": "L1", "state": "stopped_after_test", "exit_code": None, "performance": sampler.stop()}

    def test_benchmark_records_real_cpu_and_mem_and_unavailable_fps(self):
        launcher = self.FakeLauncher()
        profile = {"graphics": {"renderer": "unknown", "accelerated": None}, "wine": {"version": "wine-11"}}
        with unittest.mock.patch("arch_games.performance.fps.shutil.which", return_value=None):
            entry = service.benchmark(self.config, self.db, launcher, "g:1", 5, say=lambda m: None, profile=profile)
        self.assertEqual(launcher.calls[0], ("plan", {}))
        self.assertEqual(launcher.calls[1], ("launch", 5))
        self.assertGreater(entry["metrics"]["samples"], 0)
        self.assertFalse(entry["fps"]["available"])  # no mechanism: never estimated
        self.assertEqual(entry["kind"], "benchmark")
        self.assertEqual(service.history(self.db, "g:1")[0]["id"], entry["id"])
        self.assertEqual(service.aggregate(service.history(self.db, "g:1"))["runs"], 1)

    def test_benchmark_bounds_and_mangohud_env(self):
        for bad in (0, 4, 601):
            with self.assertRaises(AppError):
                service.benchmark(self.config, self.db, self.FakeLauncher(), "g:1", bad)
        launcher = self.FakeLauncher()
        with unittest.mock.patch("arch_games.performance.fps.shutil.which", return_value="/usr/bin/mangohud"):
            entry = service.benchmark(self.config, self.db, launcher, "g:1", 5, say=lambda m: None, profile={})
        self.assertIn("MANGOHUD_CONFIG", launcher.calls[0][1])
        self.assertFalse(entry["fps"]["available"])  # mechanism present but it produced no data -> honest failure
        self.assertIn("no frame data", entry["fps"]["reason"])


class CliTests(Base):
    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from arch_games.cli import main

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", str(Path(self.temp.name) / "s"), "--non-interactive", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_benchmark_never_runs_without_explicit_confirmation(self):
        self.db.close()
        code, out, err = self.run_cli("benchmark", "local:x")
        self.assertEqual(code, 1)
        self.assertIn("never run automatically", err)
        self.db = Database(self.config.data_dir / "state.db")

    def test_notes_tags_title_playtime_commands(self):
        import json

        self.db.put("installs", "local:x", {"directory": "d", "game": {"id": "local:x", "title": "X", "source": "local"}, "archive_sha256": "a"})
        launch(self.db, "a", "local:x", 0, 1800)
        self.db.close()
        self.assertEqual(self.run_cli("tag", "add", "local:x", "rpg")[0], 0)
        self.assertEqual(json.loads(self.run_cli("tag", "add", "local:x", "gog")[1]), ["gog", "rpg"])
        self.assertEqual(self.run_cli("tag", "add", "local:x", "bad;tag")[0], 1)
        self.assertEqual(self.run_cli("notes", "local:x", "good game")[0], 0)
        self.assertEqual(json.loads(self.run_cli("notes", "local:x")[1])["text"], "good game")
        self.assertEqual(self.run_cli("title", "set", "local:x", "Renamed")[0], 0)
        play = json.loads(self.run_cli("playtime", "Renamed")[1])
        self.assertEqual(play["local:x"]["hours"], 0.5)
        hist = json.loads(self.run_cli("history", "local:x")[1])
        self.assertEqual(hist[0]["seconds"], 1800)
        perf = json.loads(self.run_cli("performance", "local:x")[1])
        self.assertIsNone(perf["aggregate"])
        self.db = Database(self.config.data_dir / "state.db")


import unittest.mock  # noqa: E402
