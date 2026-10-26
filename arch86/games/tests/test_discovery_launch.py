"""Undertale-shaped GOG layout in a synthetic prefix; a fake `wine` records its invocation.

Nothing here runs Wine or a Windows binary."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.runtime.discovery import classify_name, confidence, discover, win_to_unix
from arch_games.runtime.installer import directory_name
from arch_games.runtime.launchers import LauncherStore
from arch_games.storage.database import Database
from test_detection import pe
from test_prefix_recovery import healthy_files

GAME = "gamevault:32"


def build_undertale(config, db):
    name = directory_name(GAME, GAME)
    prefix = config.directory("prefixes") / name
    prefix.mkdir()
    healthy_files(prefix)
    game_dir = prefix / "drive_c" / "GOG Games" / "Undertale"
    game_dir.mkdir(parents=True)
    (game_dir / "UNDERTALE.exe").write_bytes(pe(32) + bytes(2_000_000))
    (game_dir / "unins000.exe").write_bytes(pe(32) + b"Inno Setup Setup Data" + bytes(300_000))
    (game_dir / "data.win").write_bytes(b"x" * 10)
    (game_dir / "audiogroup1.dat").write_bytes(b"x")
    (game_dir / "mus.ogg").write_bytes(b"x")
    (game_dir / "goggame-1207659032.info").write_text(
        json.dumps({"name": "Undertale", "playTasks": [{"isPrimary": True, "category": "game", "path": "UNDERTALE.exe"}]})
    )
    with (prefix / "system.reg").open("a") as reg:
        reg.write(
            '\n[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\1207659032_is1] 1700000000\n'
            '"DisplayName"="Undertale"\n"InstallLocation"="C:\\\\GOG Games\\\\Undertale"\n'
        )
    db.put(
        "prefixes",
        GAME,
        {"directory": name, "state": "ready", "requested_architecture": "win64", "detected_architecture": "win64"},
    )
    return prefix, game_dir


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.prefix, self.game_dir = build_undertale(self.config, self.db)


class DiscoveryTests(Base):
    def test_classification(self):
        for name, role in {
            "UNDERTALE.exe": "game", "unins000.exe": "uninstaller", "setup_game.exe": "installer",
            "UnityCrashHandler64.exe": "crash_reporter", "vcredist_x86.exe": "redistributable",
            "Updater.exe": "updater", "config.exe": "config_utility", "CefSubProcess.exe": "launch_helper",
            "Celeste.exe": "game", "Portal2.exe": "game",
        }.items():
            self.assertEqual(classify_name(name), role, name)

    def test_undertale_ranked_first_uninstaller_excluded(self):
        found = discover(self.config, self.db, GAME, "Undertale")
        self.assertEqual([c["name"] for c in found], ["UNDERTALE.exe"])
        self.assertEqual(confidence(found), "high")
        text = " ".join(found[0]["evidence"])
        self.assertIn("GOG metadata", text)
        self.assertIn("matches the game title", text)
        everything = discover(self.config, self.db, GAME, "Undertale", include_excluded=True)
        roles = {c["name"]: c["role"] for c in everything}
        self.assertEqual(roles["unins000.exe"], "uninstaller")
        self.assertLess(everything[-1]["score"], 0)

    def test_registry_location_mapping_and_traversal(self):
        self.assertEqual(win_to_unix(self.prefix, "C:\\\\GOG Games\\\\undertale"), self.game_dir)
        self.assertIsNone(win_to_unix(self.prefix, "C:\\..\\..\\etc"))
        self.assertIsNone(win_to_unix(self.prefix, "D:\\GOG Games"))

    def test_symlinks_not_followed(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "Evil.exe").write_bytes(pe(32) + bytes(2_000_000))
        (self.game_dir / "link").symlink_to(outside)
        names = [c["name"] for c in discover(self.config, self.db, GAME, "Undertale")]
        self.assertNotIn("Evil.exe", names)

    def test_uncertain_when_two_plausible(self):
        (self.game_dir / "goggame-1207659032.info").unlink()
        (self.game_dir / "Other.exe").write_bytes(pe(32) + bytes(2_000_000))
        found = discover(self.config, self.db, GAME, "Undertale")
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["name"], "UNDERTALE.exe")

    def test_previous_successful_launch_boosts(self):
        (self.game_dir / "goggame-1207659032.info").unlink()
        other = self.game_dir / "Other.exe"
        other.write_bytes(pe(32) + bytes(2_000_000))
        self.assertEqual(discover(self.config, self.db, GAME, "Undertale")[0]["name"], "UNDERTALE.exe")
        self.db.put("launches", "x", {"game_id": GAME, "exit_code": 0, "executable": str(other.resolve()), "started_at": "t"})
        first = discover(self.config, self.db, GAME, "Undertale")[0]
        self.assertEqual(first["name"], "Other.exe")
        self.assertTrue(any("before" in e for e in first["evidence"]))
        self.db.put("launches", "x", {"game_id": GAME, "exit_code": 1, "executable": str(other.resolve()), "started_at": "t"})
        self.assertEqual(discover(self.config, self.db, GAME, "Undertale")[0]["name"], "UNDERTALE.exe")


class RegistrationTests(Base):
    def store(self):
        return LauncherStore(self.config, self.db)

    def test_register_persists_everything(self):
        record = self.store().register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"), evidence=["e"], arguments=["-w"], env={"WINEDEBUG": "-all"})
        self.assertEqual(record["runner"], "wine")
        self.assertEqual(record["prefix"], GAME)
        self.assertEqual(record["runtime_architecture"], "win64")
        self.assertEqual(record["working_directory"], str(self.game_dir.resolve()))
        self.assertTrue(record["confirmed"])
        self.assertEqual(self.db.get("launchers", GAME)["arguments"], ["-w"])

    def test_refuses_uninstaller_and_outside_paths(self):
        with self.assertRaises(AppError):
            self.store().register(GAME, "U", str(self.game_dir / "unins000.exe"))
        outside = Path(self.temp.name) / "x.exe"
        outside.write_bytes(pe(32))
        with self.assertRaises(AppError):
            self.store().register(GAME, "U", str(outside))
        with self.assertRaises(AppError):
            self.store().register(GAME, "U", "relative.exe")
        (self.game_dir / "ln.exe").symlink_to(outside)
        with self.assertRaises(AppError):
            self.store().register(GAME, "U", str(self.game_dir / "ln.exe"))
        with self.assertRaises(AppError):
            self.store().register(GAME, "U", str(self.game_dir / "UNDERTALE.exe"), env={"WINEPREFIX": "/x"})


class CliLaunchTests(Base):
    def setUp(self):
        super().setUp()
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.log = Path(self.temp.name) / "wine.log"
        wine = self.bin / "wine"
        wine.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$WINEPREFIX\" \"$PWD\" \"$@\" > '{}'\nexit 0\n".format(self.log)
        )
        wine.chmod(wine.stat().st_mode | stat.S_IXUSR)
        self.state = str(Path(self.temp.name) / "state")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "DISPLAY": ":99"}
        with patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", self.state, "--non-interactive", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_launch_by_name_end_to_end(self):
        self.db.close()
        code, _, err = self.run_cli("alias", "add", "undertale", GAME)
        self.assertEqual(code, 0, err)
        # unregistered + non-interactive: guided, never guessed
        code, _, err = self.run_cli("launch", "undertale")
        self.assertEqual(code, 1)
        self.assertIn("not confirmed", err)
        # (only the harmless `wine --version` probe may have run; the game must not have)
        self.assertNotIn("UNDERTALE.exe", self.log.read_text() if self.log.exists() else "")
        code, out, err = self.run_cli("discover", "undertale", "--register", "1")
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli("launch", "undertale", "--dry-run")
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["executable"], str((self.game_dir / "UNDERTALE.exe").resolve()))
        self.assertEqual(plan["prefix"], GAME)
        code, out, err = self.run_cli("launch", "undertale")
        self.assertEqual(code, 0, err)
        lines = self.log.read_text().splitlines()
        self.assertEqual(lines[0], str(self.prefix))
        self.assertEqual(lines[1], str(self.game_dir.resolve()))
        self.assertTrue(lines[2].endswith("UNDERTALE.exe"))
        self.assertEqual(Path(Config(self.state).data_dir / "state.db").exists(), True)
        code, out, _ = self.run_cli("info", "Undertale")
        self.assertEqual(json.loads(out)["launcher"]["runner"], "wine")
        code, out, _ = self.run_cli("prefix", "check", "undertale")
        self.assertTrue(json.loads(out)["healthy"])
        code, out, _ = self.run_cli("settings", "game", "undertale")
        self.assertEqual(json.loads(out)["aliases"], ["undertale"])

    def test_launcher_missing_executable_message(self):
        self.db.close()
        self.run_cli("launcher", "register", GAME, str(self.game_dir / "UNDERTALE.exe"))
        (self.game_dir / "UNDERTALE.exe").unlink()
        code, _, err = self.run_cli("launch", GAME)
        self.assertEqual(code, 1)
        self.assertIn("no longer exists", err)
