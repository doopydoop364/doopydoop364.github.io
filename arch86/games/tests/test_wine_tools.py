from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.diagnostics.redact import redact
from arch_games.runtime import winetools
from arch_games.runtime.launcher import Launcher
from arch_games.runtime.prefixes import PrefixManager
from arch_games.storage.database import Database
from test_discovery_launch import GAME, build_undertale


def fake_wine_tree(root, layout):
    bindir = Path(root) / "usr" / "bin"
    bindir.mkdir(parents=True)
    for tool in ("wine", "wineserver", "wineboot"):
        f = bindir / tool
        f.write_text("#!/bin/sh\necho wine-11.20\n")
        f.chmod(0o755)
    libs = {
        "wow64": ["x86_64-unix", "i386-windows", "x86_64-windows"],
        "multilib": ["x86_64-unix", "i386-unix", "i386-windows", "x86_64-windows"],
        "win32-only": ["i386-unix", "i386-windows"],
        "win64-only": ["x86_64-unix", "x86_64-windows"],
    }[layout]
    for sub in libs:
        (Path(root) / "usr" / "lib" / "wine" / sub).mkdir(parents=True)
    return str(bindir / "wine")


class LayoutTests(unittest.TestCase):
    def test_layout_classification_and_prefix_support(self):
        for layout in ("wow64", "multilib", "win32-only", "win64-only"):
            with tempfile.TemporaryDirectory() as d:
                wine = fake_wine_tree(d, layout)
                self.assertEqual(winetools.classify_layout(wine)[0], layout)
        self.assertTrue(winetools.prefix_supports("wow64", "win64", "x86_64")[0])
        self.assertFalse(winetools.prefix_supports("wow64", "win32", "x86_64")[0])  # pure WoW64 has no classic win32
        self.assertTrue(winetools.prefix_supports("win32-only", "win32", "i686")[0])
        self.assertFalse(winetools.prefix_supports("multilib", "win64", "i686")[0])

    def test_unknown_when_no_library_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bin").mkdir()
            (Path(d) / "bin" / "wine").write_text("x")
            self.assertEqual(winetools.classify_layout(str(Path(d) / "bin" / "wine"))[0], "unknown")


class RedactionTests(unittest.TestCase):
    def test_redacts_private_data(self):
        home = str(Path.home())
        text = "open {}/games/x password=hunter22 Authorization: Bearer abcdefghijkl1234 url https://u:p@host/ ip 100.64.1.2 and 127.0.0.1 box.tail1234.ts.net".format(home)
        out = redact(text, [("/data/prefix1", "<prefix>")], secrets=["tok-secret-xyz"])
        for leaked in (home + "/games", "hunter22", "abcdefghijkl1234", "u:p@", "100.64.1.2", "tail1234.ts.net"):
            self.assertNotIn(leaked, out)
        self.assertIn("127.0.0.1", out)
        self.assertIn("~/games", out)
        self.assertNotIn("tok-secret-xyz", redact("x tok-secret-xyz y", secrets=["tok-secret-xyz"]))


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)

    def test_validation(self):
        bad = [
            {"windows_version": "win95"}, {"dll_overrides": {"../x": "n"}}, {"dll_overrides": {"dxgi": "zzz"}},
            {"virtual_desktop": "big"}, {"debug": "a b; rm"}, {"env": {"PATH": "/x"}}, {"env": {"WINEPREFIX": "/x"}},
            {"runtime": "../../bin/sh"},
        ]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(AppError):
                winetools.validate_settings(values)

    def test_apply_to_plan(self):
        winetools.save_settings(self.db, "g", {"dll_overrides": {"dxgi": "n,b", "d3d9": "b"}, "debug": "-all", "virtual_desktop": "1280x720", "env": {"WINEESYNC": "1"}})
        plan = {"method": "wine", "argv": ["/usr/bin/wine", "/g/game.exe"], "env": {}}
        winetools.apply_to_plan(winetools.get_settings(self.db, "g"), plan, {})
        self.assertEqual(plan["env"]["WINEDLLOVERRIDES"], "d3d9=b;dxgi=n,b")
        self.assertEqual(plan["env"]["WINEDEBUG"], "-all")
        self.assertEqual(plan["argv"], ["/usr/bin/wine", "explorer", "/desktop=arch-games,1280x720", "/g/game.exe"])
        native = {"method": "native", "argv": ["/g/x"], "env": {}}
        winetools.apply_to_plan({"debug": "x"}, native, {})
        self.assertEqual(native["env"], {})

    def test_runtime_selection_and_registration(self):
        with tempfile.TemporaryDirectory() as d:
            wine = fake_wine_tree(d, "wow64")
            winetools.register_runtime(self.config, "wine-custom", wine)
            found = winetools.runtimes(self.config, which=lambda n: None)
            self.assertEqual(found["wine-custom"].layout, "wow64")
            plan = {"method": "wine", "argv": ["/usr/bin/wine", "g.exe"], "env": {}}
            winetools.apply_to_plan({"runtime": "wine-custom"}, plan, found)
            self.assertEqual(plan["argv"][0], wine)
            with self.assertRaises(AppError):
                winetools.apply_to_plan({"runtime": "missing"}, plan, found)
        for bad in [("system", "/bin/sh"), ("x", "relative"), ("x", "/nonexistent/wine"), ("a b", "/bin/sh")]:
            with self.assertRaises(AppError):
                winetools.register_runtime(self.config, *bad)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.config = Config(self.state)
        self.db = Database(self.config.data_dir / "state.db")
        self.prefix, self.game_dir = build_undertale(self.config, self.db)
        marker = {"game_id": GAME, "directory": self.prefix.name, "state": "ready", "requested_architecture": "win64", "detected_architecture": "win64"}
        (self.prefix / ".arch-games-prefix.json").write_text(json.dumps(marker))
        self.manager = PrefixManager(self.config, self.db)


class PrefixLifecycleTests(Base):
    def tearDown(self):
        self.db.close()

    def test_backup_restore_roundtrip_is_safe(self):
        backup = self.manager.backup(GAME)
        backup_id = Path(backup["backup"]).name
        self.assertEqual([b["id"] for b in self.manager.backups(GAME)], [backup_id])
        save = self.game_dir / "save.dat"
        save.write_text("new progress")
        preview = self.manager.restore(GAME, backup_id)
        self.assertFalse(preview["applied"])
        self.assertTrue(save.exists())  # preview changes nothing
        result = self.manager.restore(GAME, backup_id, apply=True)
        self.assertTrue(result["applied"] and result["health"])
        self.assertFalse(save.exists())
        kept = Path(result["previous_prefix_backup"])
        self.assertEqual((kept / "drive_c" / "GOG Games" / "Undertale" / "save.dat").read_text(), "new progress")
        self.assertTrue(self.manager.check(GAME)["healthy"])

    def test_restore_rejects_foreign_or_unknown_backup(self):
        backup_id = Path(self.manager.backup(GAME)["backup"]).name
        with self.assertRaises(AppError):
            self.manager.restore("gamevault:1", backup_id)
        for bad in ("nope", "../x", "prefix-missing"):
            with self.assertRaises(AppError):
                self.manager.restore(GAME, bad)

    def test_failed_restore_rolls_back(self):
        backup = Path(self.manager.backup(GAME)["backup"])
        (backup / "system.reg").write_text("garbage")  # unhealthy backup
        (self.game_dir / "marker.txt").write_text("keep me")
        with self.assertRaises(AppError):
            self.manager.restore(GAME, backup.name, apply=True)
        self.assertEqual((self.game_dir / "marker.txt").read_text(), "keep me")
        self.assertTrue(self.manager.check(GAME)["healthy"])

    def test_inspect_and_export(self):
        info = winetools.inspect_prefix(self.config, self.db, GAME)
        self.assertIn("GOG Games/Undertale", info["installed_folders"])
        self.assertGreater(info["bytes"], 2_000_000)
        exported = self.manager.export_metadata(GAME)
        self.assertEqual(exported["prefix"]["requested_architecture"], "win64")
        self.assertNotIn(str(self.state), json.dumps(exported["prefix"]))


class StatusAndLaunchTests(Base):
    def tearDown(self):
        self.db.close()

    def test_status_reports_missing_dlls_and_failed_launch(self):
        from arch_games.runtime.launchers import LauncherStore

        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"))
        self.db.put("launches", "l", {"game_id": GAME, "started_at": "t", "state": "exited", "exit_code": 53, "log": "/x"})
        result = winetools.status(self.config, self.db, GAME, which=lambda n: None)
        self.assertTrue(result["prefix"]["healthy"])
        self.assertTrue(any("exited with status 53" in p for p in result["problems"]))
        self.assertIn("arch-games wine logs " + GAME, result["suggestions"])
        self.assertIsNone(result["runtime"])

    def test_doctor_without_wine_has_actionable_failure(self):
        report = winetools.doctor(self.config, self.db, which=lambda n: None, environ={})
        wine = next(i for i in report["checks"] if i["check"] == "wine")
        self.assertEqual(wine["status"], "fail")
        self.assertIn("pacman", wine["fix"])
        self.assertTrue(any(i["check"] == "display" and i["status"] == "warn" for i in report["checks"]))

    def test_launch_plan_applies_settings(self):
        from arch_games.runtime.launchers import LauncherStore

        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"))
        winetools.save_settings(self.db, GAME, {"dll_overrides": {"dxgi": "n"}})
        launcher = Launcher(self.config, self.db, which=lambda n: "/usr/bin/" + n)
        plan = launcher.plan_registered(GAME)
        self.assertEqual(plan["env"]["WINEDLLOVERRIDES"], "dxgi=n")
        self.assertEqual(plan["env"]["WINEPREFIX"], str(self.prefix))

    def test_logs_command_redacts(self):
        log = self.config.directory("logs") / "launch-x.log"
        log.write_text("err for {} token=abc12345678 at 100.101.102.103\n".format(Path.home()))
        self.db.put("launches", "x", {"id": "x", "game_id": GAME, "started_at": "t", "state": "exited", "exit_code": 1, "log": str(log), "prefix_path": str(self.prefix)})
        self.db.close()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", str(self.state), "--non-interactive", "wine", "logs", GAME])
        self.assertEqual(code, 0, err.getvalue())
        self.assertNotIn("abc12345678", out.getvalue())
        self.assertNotIn("100.101.102.103", out.getvalue())
        self.assertIn("(redacted)", out.getvalue())
        self.db = Database(self.config.data_dir / "state.db")
