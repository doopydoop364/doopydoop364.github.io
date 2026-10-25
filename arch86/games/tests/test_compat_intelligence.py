import json
from pathlib import Path
import tempfile
import unittest

from arch_games.catalog.library import Library
from arch_games.compatibility import intelligence as ci
from arch_games.config import AppError, Config
from arch_games.models import Game
from arch_games.storage.database import Database
from test_detection import pe
from test_discovery_launch import GAME, build_undertale


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "s")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)


class MachineTests(Base):
    def make_root(self, release):
        root = Path(self.temp.name) / "root"
        (root / "etc").mkdir(parents=True, exist_ok=True)
        (root / "etc/os-release").write_text(release)
        (root / "etc/machine-id").write_text("abc123\n")
        return root

    def test_profiles_for_the_three_machines(self):
        cases = [('ID=arch\n', "x86_64", "arch64"), ('ID=archlinux32\n', "i686", "arch86"), ('ID=cachyos\n', "x86_64", "cachyos"), ('ID=debian\n', "x86_64", "debian-x86_64")]
        ids = set()
        for release, arch, name in cases:
            m = ci.machine_identity(self.config, self.make_root(release), arch)
            self.assertEqual(m["name"], name)
            ids.add(m["id"])
        self.assertEqual(len(ids), 1)  # same machine-id file -> same id; the name is only a label
        self.config.values["machine_name"] = "my-vm"
        self.assertEqual(ci.machine_identity(self.config, self.make_root("ID=arch\n"), "x86_64")["name"], "my-vm")
        with self.assertRaises(AppError):
            ci.set_machine_name(self.config, "bad name!")

    def test_id_generated_locally_without_machine_id(self):
        root = Path(self.temp.name) / "empty"
        root.mkdir()
        a = ci.machine_identity(self.config, root, "x86_64")
        b = ci.machine_identity(self.config, root, "x86_64")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(oct((self.config.data_dir / "machine-id").stat().st_mode & 0o777), "0o600")


class ClassifyTests(unittest.TestCase):
    def report(self, status, evidence=(), uncertainties=()):
        return {"status": status, "evidence": list(evidence), "uncertainties": list(uncertainties)}

    def test_matrix(self):
        rec = lambda **k: dict({"gameplay_status": "unverified", "launch_status": "never", "audio_status": "unknown"}, **k)
        cases = [
            (self.report("Promising"), None, "Likely compatible"),
            (self.report("Promising"), rec(gameplay_status="works"), "Verified working"),
            (self.report("Promising"), rec(gameplay_status="works", audio_status="issues"), "Working with issues"),
            (self.report("Promising"), rec(gameplay_status="issues"), "Working with issues"),
            (self.report("Promising"), rec(gameplay_status="fails"), "Likely incompatible"),
            (self.report("Promising"), rec(gameplay_status="slow"), "Likely incompatible"),
            (self.report("Experimental", uncertainties=["x"]), None, "Unknown"),  # executable not measured
            (self.report("Experimental"), rec(launch_status="exited_ok"), "Likely compatible"),  # started != verified
            (self.report("Unknown"), None, "Unknown"),
            (self.report("Unlikely", ["Software rendering is unlikely to provide reasonable 3D performance"]), None, "Likely incompatible"),
            (self.report("Unsupported", ["Wine is not installed"]), None, "Requires configuration"),
            (self.report("Unsupported", ["Graphical session is required but not running"]), None, "Requires configuration"),
            (self.report("Unsupported", ["64-bit executable requires a 64-bit operating system"]), None, "Likely incompatible"),
            (self.report("Unsupported", ["Foreign executable architecture: arm"]), None, "Unsupported"),
            (self.report("Unsupported", ["Wine is not installed"]), rec(gameplay_status="works"), "Requires configuration"),
        ]
        for report, record, expected in cases:
            self.assertEqual(ci.classify(report, record)[0], expected, (report, record))

    def test_installing_or_starting_never_verifies(self):
        for launch in ("started", "exited_ok"):
            cls, _ = ci.classify(self.report("Promising"), {"gameplay_status": "unverified", "launch_status": launch, "installation_status": "installed"})
            self.assertEqual(cls, "Likely compatible")

    def test_installed_without_launcher_needs_configuration(self):
        self.assertEqual(ci.classify(self.report("Unknown"), None, installed_without_launcher=True)[0], "Requires configuration")


class RecordTests(Base):
    def test_update_validates_and_timestamps_each_fact(self):
        r = ci.update(self.db, self.config, "g:1", title="G", installation_status="installed")
        self.assertIn("installation", r["evidence_times"])
        self.assertNotIn("gameplay", r["evidence_times"])
        r = ci.update(self.db, self.config, "g:1", gameplay_status="works", issue="crackle on exit")
        self.assertIn("gameplay", r["evidence_times"])
        self.assertEqual(r["known_issues"][0]["text"], "crackle on exit")
        for bad in ({"gameplay_status": "great"}, {"launch_status": "x"}, {"audio_status": 5}):
            with self.assertRaises(AppError):
                ci.update(self.db, self.config, "g:1", **bad)
        with self.assertRaises(AppError):
            ci.update(self.db, self.config, "g:1", issue="x" * 501)

    def test_records_are_machine_specific_and_import_never_touches_own(self):
        mine = ci.update(self.db, self.config, "g:1", gameplay_status="works")
        foreign = json.loads(json.dumps(mine))
        foreign["machine"] = {"id": "other-machine", "name": "arch86", "architecture": "i686"}
        foreign["gameplay_status"] = "fails"
        foreign["updated_at"] = "2999-01-01T00:00:00+00:00"
        own_as_foreign = json.loads(json.dumps(foreign))
        own_as_foreign["machine"]["id"] = mine["machine"]["id"]
        result = ci.import_records(self.db, self.config, {"schema": 1, "records": [foreign, own_as_foreign, {"junk": 1}, dict(foreign, gameplay_status="bogus")]})
        self.assertEqual(result, {"merged": 1, "skipped": 3})
        self.assertEqual(ci.get(self.db, mine["machine"]["id"], "g:1")["gameplay_status"], "works")
        self.assertEqual(ci.get(self.db, "other-machine", "g:1")["gameplay_status"], "fails")
        older = dict(foreign, updated_at="2000-01-01T00:00:00+00:00", gameplay_status="works")
        self.assertEqual(ci.import_records(self.db, self.config, {"schema": 1, "records": [older]})["merged"], 0)
        with self.assertRaises(AppError):
            ci.import_records(self.db, self.config, {"schema": 2})

    def test_export_roundtrip_has_no_secrets(self):
        ci.update(self.db, self.config, "g:1", gameplay_status="works")
        data = ci.export(self.db, self.config)
        self.assertEqual(data["schema"], 1)
        text = json.dumps(data)
        self.assertNotIn(str(self.config.data_dir), text)


class LibraryIntegrationTests(Base):
    def test_undertale_with_registered_launcher_is_measured_not_unknown(self):
        from arch_games.runtime.launchers import LauncherStore

        prefix, game_dir = build_undertale(self.config, self.db)
        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(game_dir / "UNDERTALE.exe"))
        profile = {"os_architecture": "x86_64", "os": "linux", "wine": {"available": True, "wow64": True, "win64": True, "version": "wine-11.20"}, "graphics": {"display": True, "accelerated": False, "renderer": "llvmpipe"}, "ram_total": 4 * 2**30, "ram_available": 3 * 2**30}
        lib = Library(self.config, self.db, profile).load(offline=True)
        report = lib.reports[GAME]
        self.assertEqual(report["facts"]["game_executable"]["architecture"], "i686")  # PE32 game
        self.assertEqual(report["classification"], "Likely compatible")
        self.assertEqual(report["facts"]["host"]["renderer"], "llvmpipe")
        ci.update(self.db, self.config, GAME, gameplay_status="works", audio_status="works")
        lib = Library(self.config, self.db, profile).load(offline=True)
        self.assertEqual(lib.reports[GAME]["classification"], "Verified working")
        self.assertEqual(lib.reports[GAME]["facts"]["user_confirmed"]["audio"], "works")

    def test_other_machine_success_does_not_verify_here(self):
        from arch_games.runtime.launchers import LauncherStore

        prefix, game_dir = build_undertale(self.config, self.db)
        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(game_dir / "UNDERTALE.exe"))
        mine = ci.update(self.db, self.config, GAME, launch_status="never")
        other = json.loads(json.dumps(mine))
        other["machine"] = {"id": "arch64-machine", "name": "arch64", "architecture": "x86_64"}
        other["gameplay_status"] = "works"
        ci.import_records(self.db, self.config, {"schema": 1, "records": [other]})
        profile = {"os_architecture": "i686", "os": "linux", "wine": {"available": True, "win32": True, "version": "w"}, "graphics": {"display": True}, "ram_total": 2 * 2**30}
        lib = Library(self.config, self.db, profile).load(offline=True)
        report = lib.reports[GAME]
        self.assertNotEqual(report["classification"], "Verified working")
        self.assertEqual(report["facts"]["other_machines"][0]["gameplay"], "works")
        self.assertEqual(report["facts"]["other_machines"][0]["machine"], "arch64")

    def test_missing_wine_requires_configuration(self):
        from arch_games.runtime.launchers import LauncherStore

        prefix, game_dir = build_undertale(self.config, self.db)
        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(game_dir / "UNDERTALE.exe"))
        profile = {"os_architecture": "x86_64", "os": "linux", "wine": {"available": False}, "graphics": {"display": True}, "ram_total": 2**32}
        lib = Library(self.config, self.db, profile).load(offline=True)
        self.assertEqual(lib.reports[GAME]["classification"], "Requires configuration")
