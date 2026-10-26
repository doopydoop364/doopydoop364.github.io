import json
from pathlib import Path
import tempfile
import unittest

from arch_games.cli import main
from arch_games.config import AppError, Config
from arch_games.saves.service import SaveManager, walk_files
from arch_games.storage.database import Database
from test_discovery_launch import GAME, build_undertale


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.config = Config(Path(self.temp.name) / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.prefix, self.game_dir = build_undertale(self.config, self.db)
        from arch_games.runtime.launchers import LauncherStore

        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"))
        user = self.prefix / "drive_c" / "users" / "player"
        self.saves = user / "AppData" / "Local" / "UNDERTALE"
        self.saves.mkdir(parents=True)
        (self.saves / "file0").write_text("slot0")
        (self.saves / "undertale.ini").write_text("[General]\nName=Frisk\n")
        (self.saves / "nested").mkdir()
        (self.saves / "nested" / "x.dat").write_bytes(b"\x00\x01")
        self.manager = SaveManager(self.config, self.db)
        patcher = unittest.mock.patch("pathlib.Path.home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)


import unittest.mock  # noqa: E402


class DiscoveryTests(Base):
    def test_finds_undertale_save_folder_with_evidence(self):
        found = self.manager.discover(GAME, "Undertale")
        self.assertEqual([c["path"] for c in found], [str(self.saves)])
        self.assertEqual(found[0]["files"], 3)
        self.assertEqual(found[0]["confidence"], "high")
        self.assertIn("Wine prefix user profile", found[0]["evidence"][0])
        self.assertEqual(self.manager.paths(GAME), [])  # discovery registers nothing

    def test_symlinked_profile_folders_are_never_followed(self):
        private = Path(self.temp.name) / "private"
        (private / "Undertale").mkdir(parents=True)
        (private / "Undertale" / "secret.txt").write_text("not a save")
        docs = self.prefix / "drive_c" / "users" / "player" / "Documents"
        docs.symlink_to(private)  # Wine often links Documents to the real home
        self.assertNotIn(str(private / "Undertale"), [c["path"] for c in self.manager.discover(GAME, "Undertale")])
        (self.saves / "link").symlink_to(private)
        files, skipped, _ = walk_files(self.saves)
        self.assertNotIn("link/Undertale/secret.txt", [f[0] for f in files])
        self.assertTrue(any("symlinked directory" in s for s in skipped))

    def test_linux_native_location(self):
        (self.home / ".local/share/undertale").mkdir(parents=True)
        (self.home / ".local/share/undertale/s").write_text("x")
        paths = [c["path"] for c in self.manager.discover(GAME, "Undertale")]
        self.assertIn(str(self.home / ".local/share/undertale"), paths)

    def test_unconventional_location_needs_manual_path(self):
        shutil_target = self.game_dir / "weird_place"
        shutil_target.mkdir()
        self.assertEqual([c["path"] for c in self.manager.discover(GAME, "Undertale")], [str(self.saves)])
        self.manager.add_path(GAME, str(shutil_target))
        self.assertEqual(self.manager.paths(GAME)[0]["origin"], "manual")

    def test_manual_path_validation(self):
        for bad in ("relative/dir", "/", str(self.home), str(self.home.parent), "/etc", "/nonexistent/dir"):
            with self.assertRaises(AppError, msg=bad):
                self.manager.add_path(GAME, bad)
        link = Path(self.temp.name) / "link"
        link.symlink_to(self.saves)
        with self.assertRaises(AppError):
            self.manager.add_path(GAME, str(link))
        self.manager.add_path(GAME, str(self.saves))
        self.manager.add_path(GAME, str(self.saves))  # idempotent
        self.assertEqual(len(self.manager.paths(GAME)), 1)
        self.manager.remove_path(GAME, str(self.saves))
        with self.assertRaises(AppError):
            self.manager.remove_path(GAME, str(self.saves))
        self.assertTrue(self.saves.exists())


class BackupRestoreTests(Base):
    def setUp(self):
        super().setUp()
        self.manager.add_path(GAME, str(self.saves))

    def test_backup_manifest_and_verify(self):
        m = self.manager.backup(GAME)
        self.assertEqual(m["reason"], "manual")
        files = {f["rel"] for f in m["sources"][0]["files"]}
        self.assertEqual(files, {"file0", "undertale.ini", "nested/x.dat"})
        self.assertTrue(m["created_at"].endswith("+00:00"))
        listing = self.manager.list_backups(GAME)
        self.assertEqual((len(listing), listing[0]["files"]), (1, 3))
        self.assertTrue(self.manager.verify(GAME, m["id"])["ok"])
        self.assertEqual(oct(Path(m["location"]).stat().st_mode & 0o777), "0o700")
        stored = Path(m["location"]) / "data" / "0" / "file0"
        stored.write_text("tampered")
        self.assertEqual(self.manager.verify(GAME, m["id"])["damaged_files"], ["file0"])

    def test_restore_preview_changes_nothing_and_apply_makes_safety_backup(self):
        m = self.manager.backup(GAME)
        (self.saves / "file0").write_text("newer progress")
        (self.saves / "extra.sav").write_text("created after")
        preview = self.manager.restore(GAME, m["id"])
        self.assertFalse(preview["applied"])
        self.assertEqual((preview["overwrites"], preview["identical"], preview["new"]), (1, 2, 0))
        self.assertEqual((self.saves / "file0").read_text(), "newer progress")
        result = self.manager.restore(GAME, m["id"], confirm=True)
        self.assertTrue(result["applied"])
        self.assertEqual((self.saves / "file0").read_text(), "slot0")
        self.assertEqual((self.saves / "extra.sav").read_text(), "created after")  # never deletes
        safety = json.loads((Path(result["safety_backup"]) / "manifest.json").read_text())
        self.assertEqual(safety["reason"], "pre-restore")
        stored = Path(result["safety_backup"]) / "data" / "0" / "file0"
        self.assertEqual(stored.read_text(), "newer progress")  # the overwritten data is recoverable

    def test_restore_refuses_damaged_backup_and_symlink_targets(self):
        m = self.manager.backup(GAME)
        (Path(m["location"]) / "data" / "0" / "file0").write_text("bad")
        (self.saves / "file0").write_text("keep")
        with self.assertRaises(AppError):
            self.manager.restore(GAME, m["id"], confirm=True)
        self.assertEqual((self.saves / "file0").read_text(), "keep")
        m2 = self.manager.backup(GAME)  # backup of the (modified) current state is fine
        outside = Path(self.temp.name) / "outside"
        outside.write_text("do not touch")
        (self.saves / "file0").unlink()
        (self.saves / "file0").symlink_to(outside)
        with self.assertRaises(AppError):
            self.manager.restore(GAME, m2["id"], confirm=True)
        self.assertEqual(outside.read_text(), "do not touch")

    def test_manifest_cannot_redirect_restore_to_unregistered_folder(self):
        m = self.manager.backup(GAME)
        manifest_path = Path(m["location"]) / "manifest.json"
        data = json.loads(manifest_path.read_text())
        victim = self.home / ".ssh"
        victim.mkdir()
        (victim / "authorized_keys").write_text("original")
        data["sources"][0]["path"] = str(victim)
        manifest_path.write_text(json.dumps(data))
        with self.assertRaises(AppError):
            self.manager.restore(GAME, m["id"], confirm=True)
        self.assertEqual((victim / "authorized_keys").read_text(), "original")

    def test_malicious_manifest_paths_rejected(self):
        m = self.manager.backup(GAME)
        manifest_path = Path(m["location"]) / "manifest.json"
        data = json.loads(manifest_path.read_text())
        data["sources"][0]["files"][0]["rel"] = "../../escape"
        manifest_path.write_text(json.dumps(data))
        with self.assertRaises(AppError):
            self.manager.preview(GAME, m["id"])
        for bad in ("../x", "nope", "20260101T000000Z-manual-zzzzzz", "x/y"):
            with self.assertRaises(AppError):
                self.manager.manifest(GAME, bad)

    def test_backup_requires_registered_paths_and_enforces_limits(self):
        with self.assertRaises(AppError):
            self.manager.backup("gamevault:99")
        with unittest.mock.patch("arch_games.saves.service.MAX_BYTES", 5), self.assertRaises(AppError):
            self.manager.backup(GAME)
        self.assertEqual(self.manager.list_backups(GAME), [])  # failed backups leave nothing behind
        self.assertEqual([p.name for p in self.manager.backup_root(GAME).iterdir() if p.name.startswith(".")], [])

    def test_backup_if_known_hook(self):
        messages = []
        self.assertIsNotNone(self.manager.backup_if_known(GAME, "pre-reinstall", say=messages.append))
        self.assertIsNone(self.manager.backup_if_known("local:other", "pre-reinstall", say=messages.append))
        self.assertTrue(any("No save locations" in m for m in messages))
        self.assertEqual([b["reason"] for b in self.manager.list_backups(GAME)], ["pre-reinstall"])

    def test_workflow_reinstall_and_prefix_remove_back_up_first(self):
        from arch_games.installation.workflow import Options, Workflow

        flow = Workflow(self.config, self.db, say=lambda m: None)
        job = {"game_id": GAME, "title": "Undertale", "plan": {"method": "register-existing"}, "source": {"inspection": {"kind": "directory"}}}
        flow.step_extract(job, Options(reinstall=True))  # register-existing returns before: no backup needed
        self.assertEqual(self.manager.list_backups(GAME), [])
        job["plan"]["method"] = "native"
        job["source"] = {"inspection": {"kind": "archive"}, "path": "/nonexistent.zip", "type": "file"}
        with self.assertRaises(Exception):
            flow.step_extract(job, Options(reinstall=True))
        self.assertEqual([b["reason"] for b in self.manager.list_backups(GAME)], ["pre-reinstall"])  # taken before the risky step

    def test_cli_flow_confirmations(self):
        self.db.close()
        state = str(Path(self.temp.name) / "state")

        def cli(*argv):
            import io
            from contextlib import redirect_stderr, redirect_stdout

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["--state-dir", state, "--non-interactive", *argv])
            return code, out.getvalue(), err.getvalue()

        code, out, _ = cli("saves", "backup", GAME)
        self.assertEqual(code, 0)
        backup_id = json.loads(out)["id"]
        code, out, _ = cli("saves", "restore", GAME, backup_id)
        self.assertEqual(json.loads(out)["applied"], False)
        code, _, err = cli("saves", "restore", GAME, backup_id, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        code, out, _ = cli("saves", "restore", GAME, backup_id, "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["applied"])
        self.db = Database(self.config.data_dir / "state.db")


class NoSavesYetTests(Base):
    """A game that has never been played has no save folder: empty results, no errors, nothing registered or created."""

    def test_unplayed_game_gives_an_empty_explained_result(self):
        import shutil

        shutil.rmtree(self.saves)  # as if Undertale had never been run
        self.assertEqual(self.manager.discover(GAME, "Undertale"), [])
        self.assertEqual(self.manager.paths(GAME), [])
        users = self.prefix / "drive_c" / "users"
        self.assertFalse((users / "player" / "AppData" / "Local" / "UNDERTALE").exists())  # discovery never creates folders
        with self.assertRaises(AppError) as ctx:
            self.manager.backup(GAME)
        self.assertIn("No save locations are registered", str(ctx.exception))
        self.db.close()
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(["--state-dir", str(Path(self.temp.name) / "state"), "--non-interactive", "saves", "discover", GAME])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["candidates"], [])
        self.assertIn("has not been played and saved yet", data["note"])
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--state-dir", str(Path(self.temp.name) / "state"), "--non-interactive", "saves", "paths", GAME]), 0)
        self.assertEqual(json.loads(out.getvalue()), [])
        self.db = Database(self.config.data_dir / "state.db")

    def test_nonexistent_registered_folder_is_refused_not_created(self):
        with self.assertRaises(AppError):
            self.manager.add_path(GAME, str(self.prefix / "drive_c" / "does" / "not" / "exist"))
        self.assertFalse((self.prefix / "drive_c" / "does").exists())
