"""Prefix lifecycle regressions. All runtimes/data are controlled test fixtures."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.runtime.installer import directory_name
from arch_games.runtime.launcher import Launcher
from arch_games.runtime.prefixes import PrefixManager
from arch_games.storage.database import Database
from test_detection import pe


def healthy_files(path, arch="win64"):
    (path / "drive_c").mkdir(exist_ok=True)
    for name in ["system.reg", "user.reg"]:
        (path / name).write_text("WINE REGISTRY Version 2\n#arch=" + arch + "\n")


class Clock:
    def __init__(self):
        self.value = 0.0
        self.on_sleep = None

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds
        if self.on_sleep:
            self.on_sleep()


def fake_tools(root):
    """Real child process persists registry only AFTER wineboot has exited.

    A handshake, rather than a guessed sleep duration, controls the ordering.
    These Python helpers do not run Wine or any Windows executable.
    """
    root.mkdir()
    writer = root / "writer.py"
    writer.write_text("""import os,time
from pathlib import Path
p=Path(os.environ['WINEPREFIX'])
end=time.monotonic()+5
while not (p/'.flush-request').exists():
    if time.monotonic()>=end: raise SystemExit(2)
    time.sleep(.005)
for name in ['system.reg','user.reg']:
    (p/name).write_text('WINE REGISTRY Version 2\\n#arch='+os.environ['WINEARCH']+'\\n')
(p/'.flush-done').touch()
""")
    helper = """import os,sys,subprocess,time
from pathlib import Path
name=Path(sys.argv[0]).name
if name=='wine':
    print('wine-11.20 synthetic-process-fixture')
elif name=='wineboot':
    p=Path(os.environ['WINEPREFIX']);(p/'drive_c').mkdir()
    print('Mesa DRI3 warning; err:ole RPC synthetic warning',flush=True)
    subprocess.Popen([sys.executable,str(Path(__file__).with_name('writer.py'))],
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL)
elif name=='wineserver':
    assert sys.argv[1:]==['-w']
    p=Path(os.environ['WINEPREFIX']);(p/'.flush-request').touch()
    end=time.monotonic()+5
    while not (p/'.flush-done').exists():
        if time.monotonic()>=end: raise SystemExit(3)
        time.sleep(.005)
"""
    for name in ["wine", "wineboot", "wineserver"]:
        script = root / name
        script.write_text("#!" + sys.executable + "\n" + helper)
        script.chmod(0o700)
    return {name: str(root / name) for name in ["wine", "wineboot", "wineserver"]}


class PrefixRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="prefix recovery ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.clock = Clock()
        self.calls = []
        self.manager = PrefixManager(
            self.config,
            self.db,
            self.runner,
            lambda name: "/fixture/" + name,
            self.clock.now,
            self.clock.sleep,
        )
        self.ident = "gamevault:32"

    def runner(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if "--version" in args:
            return SimpleNamespace(returncode=0, stdout="wine-11.20 fixture")
        path = Path(kwargs["env"]["WINEPREFIX"])
        if args[0].endswith("wineboot"):
            (path / "drive_c").mkdir(exist_ok=True)
        elif args[0].endswith("wineserver"):
            self.assertEqual(args[1:], ["-w"])
            healthy_files(path)
        return SimpleNamespace(returncode=0)

    def seed(self, state="failed", arch="win64", healthy=True):
        name = directory_name(self.ident, self.ident)
        path = self.manager.root / name
        path.mkdir()
        if healthy:
            healthy_files(path, arch)
        (path / "save.dat").write_bytes(b"Preserve user data")
        self.db.put(
            "prefixes",
            self.ident,
            {
                "instance_id": "original-instance",
                "game_id": self.ident,
                "directory": name,
                "state": state,
                "requested_architecture": arch,
                "wine_version": "wine-original",
                "log": "original.log",
                "environment": {"WINEDEBUG": "-all"},
                "last_failure": {"kind": "verification", "issues": ["system.reg: missing"]},
                "custom_metadata": "retain",
            },
        )
        return path

    def test_real_process_handshake_waits_for_registry_flush(self):
        tools = fake_tools(self.root / "tools")
        observed = []

        def run(args, **kwargs):
            result = subprocess.run(args, **kwargs)
            if Path(args[0]).name == "wineboot":
                path = Path(kwargs["env"]["WINEPREFIX"])
                observed.append(
                    (result.returncode, (path / "system.reg").exists(), (path / "user.reg").exists())
                )
            return result

        manager = PrefixManager(self.config, self.db, run, tools.get)
        record = manager.create(self.ident)
        self.assertEqual(observed, [(0, False, False)])
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["initialization"]["wineserver_exit"], 0)
        self.assertIn("DRI3", Path(record["log"]).read_text())
        self.assertIn("err:ole", Path(record["log"]).read_text())
        self.assertTrue(manager.check(self.ident)["healthy"])

    def test_create_orders_commands_and_scopes_wait_to_prefix(self):
        record = self.manager.create(self.ident)
        self.assertEqual(
            [args for args, _ in self.calls],
            [["/fixture/wine", "--version"], ["/fixture/wineboot", "-u"], ["/fixture/wineserver", "-w"]],
        )
        self.assertEqual(self.calls[-1][1]["env"]["WINEPREFIX"], str(self.manager.path(self.ident)))
        self.assertLessEqual(self.calls[-1][1]["timeout"], 30)
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["initialization"]["verification_attempts"], 2)

    def test_bounded_polling_after_server_exit_does_not_reinitialize(self):
        def run(args, **kwargs):
            self.calls.append((args, kwargs))
            if "--version" in args:
                return SimpleNamespace(returncode=0, stdout="fixture")
            return SimpleNamespace(returncode=0)

        self.manager.run = run
        self.clock.on_sleep = (
            lambda: healthy_files(self.manager.path(self.ident)) if self.clock.value <= 0.11 else None
        )
        record = self.manager.create(self.ident)
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["initialization"]["verification_attempts"], 3)
        self.assertEqual(sum("wineboot" in args[0] for args, _ in self.calls), 1)

    def test_readonly_check_exposes_stale_state_without_changing_it(self):
        path = self.seed()
        original = self.db.get("prefixes", self.ident)
        snapshot = (path / "system.reg").stat().st_mtime_ns
        result = self.manager.check(self.ident)
        self.assertTrue(result["healthy"])
        self.assertTrue(result["recovery_available"])
        self.assertIn("prefix recover", result["next_action"])
        self.assertEqual(self.db.get("prefixes", self.ident), original)
        self.assertEqual((path / "system.reg").stat().st_mtime_ns, snapshot)
        self.assertEqual(self.calls, [])

    def test_recovery_preserves_prefix_data_identity_and_failure_details(self):
        path = self.seed()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir() if p.is_file()}
        inode = path.stat().st_ino
        self.manager.which = lambda name: self.fail("Recovery must not invoke/locate Wine")
        self.manager.run = lambda *a, **k: self.fail("Recovery must not run Wine")
        record = self.manager.recover(self.ident)
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["instance_id"], "original-instance")
        self.assertEqual(record["custom_metadata"], "retain")
        self.assertEqual(record["log"], "original.log")
        self.assertEqual(record["last_failure"]["kind"], "verification")
        self.assertEqual(path.stat().st_ino, inode)
        for name, data in before.items():
            self.assertEqual(((path / name).read_bytes(), (path / name).stat().st_mtime_ns), data)
        self.assertEqual(json.loads((path / ".arch-games-prefix.json").read_text())["state"], "ready")
        self.assertEqual(self.manager.recover(self.ident)["state"], "ready")

    def test_repeated_create_recovers_failed_or_interrupted_record_without_wine(self):
        for state in ["failed", "creating"]:
            with self.subTest(state=state):
                if not self.db.get("prefixes", self.ident):
                    self.seed(state=state)
                else:
                    record = self.db.get("prefixes", self.ident)
                    record["state"] = state
                    self.db.put("prefixes", self.ident, record)
                self.manager.which = lambda name: self.fail("No Wine invocation for recovery")
                record = self.manager.create(self.ident)
                self.assertEqual(record["state"], "ready")
                self.assertEqual(record["instance_id"], "original-instance")

    def test_missing_partial_or_mismatched_files_never_mark_ready(self):
        path = self.seed(healthy=False)
        with self.assertRaises(AppError):
            self.manager.recover(self.ident, timeout=1)
        self.assertLessEqual(self.clock.value, 1)
        self.assertEqual(self.db.get("prefixes", self.ident)["state"], "failed")
        self.assertEqual((path / "save.dat").read_bytes(), b"Preserve user data")
        healthy_files(path)
        (path / "user.reg").write_text("")
        self.assertFalse(self.manager.check(self.ident)["healthy"])
        (path / "user.reg").write_text("WINE REGISTRY Version 2\n#arch=win32\n")
        with self.assertRaises(AppError):
            self.manager.recover(self.ident)
        self.assertTrue(self.manager.check(self.ident)["architecture_mismatch"])
        self.assertEqual(self.db.get("prefixes", self.ident)["state"], "failed")

    def test_nonzero_wineboot_is_recorded_and_later_recoverable(self):
        def run(args, **kwargs):
            if "--version" in args:
                return SimpleNamespace(returncode=0, stdout="fixture")
            return SimpleNamespace(returncode=9)

        self.manager.run = run
        with self.assertRaisesRegex(AppError, "status 9"):
            self.manager.create(self.ident)
        record = self.db.get("prefixes", self.ident)
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["initialization"]["wineboot_exit"], 9)
        self.assertEqual(record["last_failure"]["stage"], "wineboot")
        healthy_files(self.manager.path(self.ident))
        self.assertEqual(self.manager.recover(self.ident)["state"], "ready")

    def test_wait_timeout_is_bounded_and_preserves_healthy_prefix_for_recovery(self):
        original = self.manager.run

        def run(args, **kwargs):
            if args[0].endswith("wineserver"):
                healthy_files(Path(kwargs["env"]["WINEPREFIX"]))
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return original(args, **kwargs)

        self.manager.run = run
        with self.assertRaisesRegex(AppError, "timed out during wineserver_wait"):
            self.manager.create(self.ident, timeout=2)
        record = self.db.get("prefixes", self.ident)
        self.assertEqual(record["last_failure"]["kind"], "timeout")
        self.assertTrue(self.manager.check(self.ident)["healthy"])
        self.assertEqual(self.manager.recover(self.ident)["state"], "ready")

    def test_launcher_directs_stale_healthy_prefix_to_recovery_then_accepts_it(self):
        self.seed()
        game_root = self.config.directory("installed") / "game"
        game_root.mkdir()
        (game_root / "Game.exe").write_bytes(pe())
        self.db.put("installs", self.ident, {"directory": "game"})
        launcher = Launcher(self.config, self.db, which=lambda name: "/fixture/" + name)
        with self.assertRaisesRegex(AppError, "prefix recover"):
            launcher.plan(self.ident, "Game.exe")
        self.manager.recover(self.ident)
        self.assertEqual(launcher.plan(self.ident, "Game.exe")["method"], "wine")

    def test_cli_recovery_without_wine_or_recreating_data(self):
        path = self.seed()
        with redirect_stdout(io.StringIO()) as output:
            code = main(["--state-dir", str(self.root / "state"), "prefix", "recover", self.ident])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["state"], "ready")
        self.assertEqual((path / "save.dat").read_bytes(), b"Preserve user data")

    def test_architecture_changes_and_unmanaged_paths_are_preserved(self):
        path = self.seed()
        with self.assertRaises(AppError):
            self.manager.create(self.ident, "win32")
        self.assertTrue((path / "save.dat").exists())
        self.db.delete("prefixes", self.ident)
        with self.assertRaisesRegex(AppError, "Unmanaged"):
            self.manager.create(self.ident)
        self.assertTrue((path / "save.dat").exists())

    def test_recovery_preserves_extra_app_metadata_and_refuses_unsafe_registries(self):
        path = self.seed()
        marker = path / ".arch-games-prefix.json"
        marker.write_text(json.dumps({"user_note": "keep this metadata"}))
        self.assertEqual(self.manager.recover(self.ident)["user_note"], "keep this metadata")
        (path / "user.reg").unlink()
        outside = self.root / "outside.reg"
        outside.write_text("WINE REGISTRY Version 2\n#arch=win64\n")
        (path / "user.reg").symlink_to(outside)
        (path / "drive_c").rmdir()
        relocated = self.root / "relocated-drive"
        relocated.mkdir()
        (path / "drive_c").symlink_to(relocated, target_is_directory=True)
        self.assertTrue(self.manager.check(self.ident)["healthy"])
        self.assertEqual(self.manager.recover(self.ident)["state"], "ready")
        (path / "user.reg").unlink()
        (path / "user.reg").mkdir()
        self.assertFalse(self.manager.check(self.ident)["healthy"])
        with self.assertRaises(AppError):
            self.manager.recover(self.ident, timeout=1)
        self.assertEqual(outside.read_text(), "WINE REGISTRY Version 2\n#arch=win64\n")

    def test_global_deadline_and_invalid_timeouts_do_not_reset_prefix(self):
        original = self.manager.run

        def run(args, **kwargs):
            result = original(args, **kwargs)
            if args[0].endswith("wineboot"):
                self.clock.value = 1.9
            if args[0].endswith("wineserver"):
                self.assertLessEqual(kwargs["timeout"], 0.100001)
            return result

        self.manager.run = run
        record = self.manager.create(self.ident, timeout=2)
        self.assertEqual(record["state"], "ready")
        self.assertLessEqual(self.clock.value, 2)
        with self.assertRaises(AppError):
            self.manager.recover(self.ident, timeout=0)
        with self.assertRaises(AppError):
            self.manager.create(self.ident, timeout=601)

    def test_event_failure_after_ready_does_not_mark_healthy_prefix_failed(self):
        original = self.db.put

        def put(namespace, key, value):
            if namespace == "events":
                raise AppError("Synthetic event failure")
            return original(namespace, key, value)

        self.db.put = put
        with self.assertRaisesRegex(AppError, "event failure"):
            self.manager.create(self.ident)
        self.assertEqual(self.db.get("prefixes", self.ident)["state"], "ready")
        self.assertTrue(self.manager.check(self.ident)["healthy"])
