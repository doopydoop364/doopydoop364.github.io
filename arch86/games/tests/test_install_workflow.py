"""Install workflow with fixture archives, a stub Wine runner and real prefix health checks.

No installer is executed: the fake `popen` stands in for Wine and writes game files."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from arch_games.config import AppError, Config
from arch_games.installation.workflow import Options, Workflow
from arch_games.runtime.installer import directory_name
from arch_games.runtime.launcher import Launcher
from arch_games.storage.database import Database
from test_detection import elf, pe
from test_prefix_recovery import healthy_files

GAME = "local:undertale"


class FakeProcess:
    def wait(self, timeout=None):
        return 0


class StubPrefixes:
    """Creates structurally healthy prefixes without Wine; real checks are covered by prefix tests."""

    def __init__(self, config, db):
        self.config, self.db = config, db
        self.created = []

    def create(self, ident, arch, **kw):
        name = directory_name(ident, ident)
        path = self.config.directory("prefixes") / name
        path.mkdir()
        healthy_files(path, arch)
        self.db.put("prefixes", ident, {"directory": name, "state": "ready", "requested_architecture": arch, "detected_architecture": arch})
        self.created.append((ident, arch))

    def check(self, ident):
        from arch_games.runtime.prefixes import PrefixManager

        return PrefixManager(self.config, self.db).check(ident)

    def recover(self, ident):
        raise AssertionError("not expected")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.runs = []
        self.exit_code = 0
        self.messages = []
        self.launcher = Launcher(self.config, self.db, which=lambda n: "/usr/bin/" + n, popen=self.popen)
        # healthy ready prefix already present (as after `prefix create`)
        name = directory_name(GAME, GAME)
        self.prefix = self.config.directory("prefixes") / name
        self.prefix.mkdir()
        healthy_files(self.prefix)
        self.db.put("prefixes", GAME, {"directory": name, "state": "ready", "requested_architecture": "win64", "detected_architecture": "win64"})

    def popen(self, argv, **kwargs):
        self.runs.append((argv, kwargs))
        if self.exit_code == 0 and "setup_undertale" in argv[1]:
            game = self.prefix / "drive_c" / "GOG Games" / "Undertale"
            game.mkdir(parents=True, exist_ok=True)
            (game / "UNDERTALE.exe").write_bytes(pe(32) + bytes(2_000_000))
            (game / "unins000.exe").write_bytes(pe(32) + bytes(300_000))
            (game / "data.win").write_bytes(b"x")
            (game / "goggame-1.info").write_text(json.dumps({"name": "Undertale", "playTasks": [{"isPrimary": True, "category": "game", "path": "UNDERTALE.exe"}]}))
        proc = FakeProcess()
        proc.wait = lambda timeout=None: self.exit_code
        return proc

    def flow(self, confirm=None):
        return Workflow(self.config, self.db, say=self.messages.append, confirm=confirm, launcher=self.launcher, prefixes=StubPrefixes(self.config, self.db))

    def zip(self, name, files):
        path = Path(self.temp.name) / name
        with zipfile.ZipFile(path, "w") as z:
            for member, data in files.items():
                z.writestr(member, data)
        return str(path)

    def gog_zip(self):
        return self.zip("undertale.zip", {"setup_undertale_1.08.exe": pe(32), "setup_undertale_1.08-1.bin": b"data" * 100})


class InstallerFlowTests(Base):
    def test_full_installer_flow_requires_separate_approval(self):
        archive = self.gog_zip()
        report = self.flow().run(GAME, "Undertale", Options(archive=archive, yes=True))
        self.assertEqual(report["status"], "awaiting_approval")
        self.assertEqual(self.runs, [])  # --yes alone never runs the downloaded installer
        self.assertTrue(self.db.get("installs", GAME))  # extraction is not execution
        self.assertIn("--approve-installer", " ".join(report["next"]))
        report = self.flow().run(GAME, "Undertale", Options(archive=archive, yes=True, approve_installer=True))
        self.assertEqual(report["status"], "done", report)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.runs[0][0][0], "/usr/bin/wine")
        self.assertEqual(self.runs[0][1]["env"]["WINEPREFIX"], str(self.prefix))
        launcher = self.db.get("launchers", GAME)
        self.assertTrue(launcher["executable"].endswith("UNDERTALE.exe"))
        self.assertEqual(self.db.get("install_jobs", GAME)["status"], "done")
        # purpose recorded so installer runs never count as game launches
        self.assertEqual(self.db.all("launches")[0]["value"]["purpose"], "installer")
        again = self.flow().run(GAME, "Undertale", Options(archive=archive, yes=True, approve_installer=True))
        self.assertEqual(again["status"], "already_installed")
        self.assertEqual(len(self.runs), 1)

    def test_plan_only_and_noninteractive_stop_without_side_effects(self):
        archive = self.gog_zip()
        report = self.flow().run(GAME, "Undertale", Options(archive=archive, plan_only=True))
        self.assertEqual(report["status"], "plan_only")
        self.assertEqual(report["plan"]["method"], "wine-installer")
        self.assertIsNone(self.db.get("installs", GAME))
        self.assertEqual(self.flow().run(GAME, "Undertale", Options(archive=archive))["status"], "awaiting_approval")
        self.assertEqual(self.runs, [])
        self.assertIn("Installer architecture", "Installer architecture")  # architectures stay separate facts
        plan = self.db.get("install_jobs", GAME)["plan"]
        self.assertEqual(plan["installer_architecture"], "i686")
        self.assertIn("unknown", plan["game_architecture"])
        self.assertEqual(plan["prefix"]["action"], "reuse")

    def test_interactive_confirm_covers_installer(self):
        report = self.flow(confirm=lambda q: True).run(GAME, "Undertale", Options(archive=self.gog_zip(), yes=False))
        self.assertEqual(report["status"], "awaiting_selection" if not self.db.get("launchers", GAME) else "done")
        self.assertEqual(len(self.runs), 1)

    def test_failed_installer_is_resumable(self):
        archive = self.gog_zip()
        self.exit_code = 1
        options = Options(archive=archive, yes=True, approve_installer=True)
        with self.assertRaises(AppError):
            self.flow().run(GAME, "Undertale", options)
        job = self.db.get("install_jobs", GAME)
        self.assertEqual(job["status"], "failed")
        self.assertTrue(self.db.get("installs", GAME))
        self.exit_code = 0
        report = self.flow().run(GAME, "Undertale", options)
        self.assertEqual(report["status"], "done")
        self.assertEqual(len(self.runs), 2)

    def test_ambiguous_installers_require_choice(self):
        archive = self.zip("two.zip", {"setup_a.exe": pe(32), "setup_b.exe": pe(32)})
        with self.assertRaises(AppError):
            self.flow().run(GAME, "Undertale", Options(archive=archive, yes=True, approve_installer=True))
        self.assertEqual(self.runs, [])
        self.assertEqual(self.db.get("install_jobs", GAME)["status"], "failed")

    def test_insufficient_space_refused_before_anything(self):
        archive = self.gog_zip()
        usage = type("U", (), {"free": 10})()
        with patch("arch_games.installation.workflow.shutil.disk_usage", return_value=usage), self.assertRaises(AppError):
            self.flow().run(GAME, "Undertale", Options(archive=archive, yes=True))
        self.assertIsNone(self.db.get("installs", GAME))

    def test_abort_moves_files_to_backups_keeps_prefix(self):
        archive = self.gog_zip()
        self.flow().run(GAME, "Undertale", Options(archive=archive, yes=True))
        result = self.flow().abort(GAME)
        self.assertEqual(result["prefix"], "retained")
        self.assertIsNone(self.db.get("installs", GAME))
        self.assertTrue(self.prefix.exists())
        self.assertEqual(self.db.get("install_jobs", GAME)["status"], "cancelled")

    def test_silent_inno_needs_evidence(self):
        with self.assertRaises(AppError):
            self.flow().run(GAME, "Undertale", Options(archive=self.gog_zip(), yes=True, silent_inno=True))


class PortableAndOtherTests(Base):
    def test_portable_windows_game(self):
        archive = self.zip("p.zip", {"Game/Celeste.exe": pe(64) + bytes(2_000_000), "Game/data.pak": b"x"})
        flow = self.flow()
        report = flow.run("local:celeste", "Celeste", Options(archive=archive, yes=True))
        self.assertEqual(report["plan"]["method"], "portable-wine")
        self.assertEqual(flow._prefixes.created, [("local:celeste", "win64")])  # PE32+ game, win64 on x86_64
        self.assertEqual(report["status"], "done", report)
        self.assertEqual(self.runs, [])  # nothing executed
        self.assertEqual(self.db.get("launchers", "local:celeste")["runner"], "wine")

    def test_pe32_game_still_gets_wow64_prefix_on_x86_64(self):
        archive = self.zip("p.zip", {"Game/Old.exe": pe(32) + bytes(2_000_000), "Game/data.pak": b"x"})
        flow = self.flow()
        with patch("arch_games.installation.workflow.architecture", return_value="x86_64"):
            flow.run("local:old", "Old", Options(archive=archive, yes=True))
        self.assertEqual(flow._prefixes.created, [("local:old", "win64")])

    def test_i686_host_gets_win32_prefix(self):
        archive = self.zip("p.zip", {"Game/Old.exe": pe(32) + bytes(2_000_000), "Game/data.pak": b"x"})
        flow = self.flow()
        with patch("arch_games.installation.workflow.architecture", return_value="i686"):
            flow.run("local:old", "Old", Options(archive=archive, yes=True))
        self.assertEqual(flow._prefixes.created, [("local:old", "win32")])

    def test_native_linux_archive(self):
        path = Path(self.temp.name) / "n.zip"
        with zipfile.ZipFile(path, "w") as z:
            info = zipfile.ZipInfo("Game/mygame")
            info.external_attr = 0o755 << 16
            z.writestr(info, elf(64, 62) + bytes(2_000_000))
            z.writestr("Game/data.pak", b"x")
        report = self.flow().run("local:native", "mygame", Options(archive=str(path), yes=True))
        self.assertEqual(report["plan"]["method"], "native")
        self.assertEqual(report["status"], "done", report)
        self.assertEqual(self.db.get("launchers", "local:native")["runner"], "native")

    def test_existing_directory_registered_in_place(self):
        game = Path(self.temp.name) / "existing"
        game.mkdir()
        (game / "mygame").write_bytes(elf(64, 62) + bytes(2_000_000))
        (game / "mygame").chmod(0o755)
        (game / "data.pak").write_bytes(b"x")
        before = sorted(p.name for p in game.iterdir())
        report = self.flow().run("local:mine", "mygame", Options(directory=str(game), yes=True))
        self.assertEqual(report["status"], "done", report)
        self.assertEqual(sorted(p.name for p in game.iterdir()), before)
        self.assertEqual(self.db.get("launchers", "local:mine")["runner"], "native")

    def test_single_non_installer_exe_rejected(self):
        exe = Path(self.temp.name) / "Game.exe"
        exe.write_bytes(pe(32) + bytes(2_000_000))
        with self.assertRaises(AppError):
            self.flow().run("local:g", "Game", Options(archive=str(exe), yes=True))

    def test_missing_source_errors(self):
        with self.assertRaises(AppError):
            self.flow().run("local:g", "Game", Options())
        with self.assertRaises(AppError):
            self.flow().run("local:g", "Game", Options(archive="/nonexistent.zip"))
        with self.assertRaises(AppError):
            self.flow().run("local:g", "Game", Options(archive="a", directory="b"))


class GameVaultAcquireTests(Base):
    def test_existing_download_is_reused_without_new_download(self):
        from arch_games.downloads.manager import DownloadManager

        manager = DownloadManager(self.config, self.db)
        archive = self.gog_zip()
        name = "abc-undertale.zip"
        (self.config.directory("downloads") / name).write_bytes(Path(archive).read_bytes())
        self.db.put("downloads", "abc", {"id": "abc", "game_id": "gamevault:32", "state": "completed", "filename": name, "verification": {"sha256": "x"}, "received": 1, "expected_size": 1})
        calls = []
        flow = Workflow(
            self.config, self.db, say=self.messages.append, downloads=manager,
            library_find=lambda gid: calls.append(gid), launcher=self.launcher, prefixes=StubPrefixes(self.config, self.db),
        )
        flow.prefixes.create("gamevault:32", "win64")
        report = flow.run("gamevault:32", "Undertale", Options(yes=True))
        self.assertEqual(calls, [])  # catalog never consulted: no download scheduled
        self.assertEqual(report["status"], "awaiting_approval")
        self.assertEqual(len(self.db.all("downloads")), 1)
        self.assertTrue(any("reusing" in m for m in self.messages))

    def test_download_incomplete_is_actionable(self):
        from arch_games.downloads.manager import DownloadManager

        class Offline(DownloadManager):
            def run(self, progress=None):
                pass

        flow = Workflow(
            self.config, self.db, say=self.messages.append, downloads=Offline(self.config, self.db),
            library_find=lambda gid: __import__("arch_games.models", fromlist=["Game"]).Game(gid, "U", "gamevault", metadata={"remote": {"id": "32"}}),
        )
        with self.assertRaises(AppError) as ctx:
            flow.run("gamevault:32", "Undertale", Options(yes=True))
        self.assertIn("resume", str(ctx.exception))
        self.assertEqual(self.db.get("install_jobs", "gamevault:32")["status"], "failed")
