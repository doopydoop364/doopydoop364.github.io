"""Regression for native-install worker discovery and a synthetic GOG-style RAR4."""

from contextlib import redirect_stderr, redirect_stdout
import ctypes
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from arch_games.catalog.library import Library
from arch_games.cli import main
from arch_games.compatibility.archive_errors import ArchiveError, redact_debug
from arch_games.compatibility.archive_worker import main as worker_main, run as worker_run
from arch_games.compatibility.archives import ArchiveLimits, inspect_archive
from arch_games.compatibility.executables import MAX_HEADER, inspect_bytes
from arch_games.config import Config
from arch_games.models import Game
from arch_games.storage.database import Database
from rar4_fixture import MEMBER, ROOT, installer_bytes, write_rar4
from test_app_install import module as app_installer
from test_compatibility import profile
from test_detection import pe

SOURCE = Path(__file__).parents[1]


class ArchiveRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archive regression ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "Example.Game.v1.08.GOG.rar"
        write_rar4(self.archive)

    def test_native_install_analyze_from_unrelated_directory_and_aliases(self):
        installer = app_installer.ApplicationInstall(
            self.root / "home", self.root / "data", self.root / "config"
        )
        installer.install(SOURCE)
        # A CWD/PYTHONPATH package must not shadow the trusted installed worker.
        spoof = self.root / "arch_games"
        spoof.mkdir()
        (spoof / "__init__.py").write_text('raise RuntimeError("Untrusted CWD package executed")')
        for alias, options in [
            ("arch-games", []),
            ("arch86-games", ["--member", MEMBER]),
            ("arch64-games", ["--member", MEMBER, "--game", "gamevault:32", "--graphics", "2d"]),
        ]:
            command = [
                str(installer.bin / alias),
                "--state-dir",
                str(self.root / "state"),
                "analyze",
                str(self.archive),
            ] + options
            result = subprocess.run(
                command,
                cwd=self.root,
                env={**os.environ, "PYTHONPATH": str(self.root)},
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            binaries = [e["executable"] for e in output["archive"]["entries"] if e.get("executable")]
            self.assertTrue(any(b["format"] == "PE32" and b["role"] == "installer" for b in binaries))
            self.assertEqual(output["report"]["status"], "Unknown")
        config = Config(self.root / "state")
        db = Database(config.data_dir / "state.db")
        self.addCleanup(db.close)
        saved = db.get("analysis", "gamevault:32")
        self.assertNotIn("executable", saved)
        self.assertEqual(saved["installer_analysis"]["member"], MEMBER)
        self.assertEqual(saved["installer_analysis"]["executable"]["architecture"], "i686")
        self.assertEqual(output["report"]["status"], "Unknown")
        self.assertEqual(output["report"]["architecture_match"], "unknown")
        self.assertEqual(output["report"]["evidence_scope"], "installer")
        self.assertTrue(self.archive.is_file())
        self.assertFalse((self.root / ROOT).exists())  # no analysis extraction/temp payload
        installed = subprocess.run(
            [
                str(installer.bin / "arch-games"),
                "--state-dir",
                str(self.root / "state"),
                "install",
                "local:synthetic",
                "--archive",
                str(self.archive),
                "--yes",
                "--extract-only",
            ],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(self.root)},
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(
            db.get("installs", "local:synthetic")["executables"][0]["executable"]["role"], "installer"
        )

    def test_rar4_directory_and_pe32_gui_installer_headers(self):
        report = inspect_archive(self.archive)
        self.assertEqual(report["scanned_entries"], 4)
        self.assertTrue(report["entries"][0]["directory"])
        binary = report["entries"][2]["executable"]
        self.assertEqual(
            (binary["format"], binary["architecture"], binary["subsystem"], binary["role"]),
            ("PE32", "i686", "Windows GUI", "installer"),
        )
        self.assertEqual(report["entries"][3]["executable"]["architecture"], "x86_64")
        self.assertEqual(inspect_bytes(pe(), name="setup_custom.exe").role, "installer")
        self.assertEqual(inspect_bytes(pe(), name="Game.exe").role, "unknown")

    def test_selected_member_stops_before_unrelated_unsafe_tail(self):
        write_rar4(self.archive, unsafe_tail=True, large_prefix=True)
        report = inspect_archive(self.archive, member=MEMBER)
        self.assertEqual(report["scanned_entries"], 3)
        self.assertEqual([e["name"] for e in report["entries"]], [MEMBER])
        self.assertLessEqual(report["entries"][0]["header_bytes_read"], MAX_HEADER)
        self.assertFalse(report["listing_complete"])
        self.assertFalse(report["complete_archive_validation"])
        with self.assertRaises(ArchiveError) as error:
            inspect_archive(self.archive)
        self.assertEqual(error.exception.kind, "unsafe_archive")

    def test_selected_member_reads_only_target_payload(self):
        library = MagicMock()
        entries = [
            (ROOT + "/unrelated.exe", b"not PE"),
            (MEMBER, installer_bytes()),
            (ROOT + "/tail.exe", pe(64)),
        ]
        current = {"index": -1, "offset": 0}

        def next_header(archive, entry):
            current["index"] += 1
            current["offset"] = 0
            entry._obj.value = 1
            return 0 if current["index"] < len(entries) else 1

        reads = []

        def read(archive, buffer, length):
            name, data = entries[current["index"]]
            reads.append(name)
            chunk = data[current["offset"] : current["offset"] + length]
            ctypes.memmove(buffer, chunk, len(chunk))
            current["offset"] += len(chunk)
            return len(chunk)

        library.archive_read_new.return_value = 1
        library.archive_read_open_filename.return_value = 0
        library.archive_read_next_header.side_effect = next_header
        library.archive_entry_pathname.side_effect = lambda _: entries[current["index"]][0].encode()
        library.archive_entry_size.side_effect = lambda _: len(entries[current["index"]][1])
        library.archive_entry_filetype.return_value = 0o100000
        for name in [
            "archive_entry_symlink",
            "archive_entry_hardlink",
            "archive_entry_is_encrypted",
            "archive_filter_bytes",
        ]:
            getattr(library, name).return_value = 0
        library.archive_read_data.side_effect = read
        with (
            patch("arch_games.compatibility.archive_worker.resource.setrlimit") as limit,
            patch("arch_games.compatibility.archive_worker.ctypes.util.find_library", return_value="fixture"),
            patch("arch_games.compatibility.archive_worker.C.CDLL", return_value=library),
        ):
            report = worker_run(self.archive, None, ArchiveLimits(), MEMBER)
        self.assertTrue(reads)
        self.assertEqual(set(reads), {MEMBER})
        self.assertEqual(report["scanned_entries"], 2)
        self.assertIn(((384 * 1024**2, 384 * 1024**2),), [call.args[1:] for call in limit.call_args_list])

    def test_persistence_keeps_existing_game_evidence_and_library_architecture(self):
        config = Config(self.root / "state")
        db = Database(config.data_dir / "state.db")
        self.addCleanup(db.close)
        real_game = inspect_bytes(pe(64)).to_dict()
        db.put("analysis", "gamevault:32", {"executable": real_game, "requirements": {"graphics": "2d"}})
        with (
            redirect_stdout(io.StringIO()) as stdout,
            patch("arch_games.platform.system_profile", return_value=profile()),
        ):
            code = main(
                [
                    "--state-dir",
                    str(self.root / "state"),
                    "analyze",
                    str(self.archive),
                    "--member",
                    MEMBER,
                    "--game",
                    "gamevault:32",
                    "--graphics",
                    "2d",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["executable"]["role"], "installer")
        saved = db.get("analysis", "gamevault:32")
        self.assertEqual(saved["executable"]["architecture"], "x86_64")
        self.assertEqual(saved["installer_analysis"]["executable"]["architecture"], "i686")
        game = Game("gamevault:32", "Example", "gamevault", os="windows")
        with patch("arch_games.catalog.library.visible", return_value=([game], "")):
            library = Library(config, db, profile()).load()
        self.assertEqual(library.games[0].architecture, "x86_64")
        # Older 2.0 records/installation manifests used the setup executable as the game.
        db.put("analysis", game.id, {"executable": inspect_bytes(pe()).to_dict(), "member": MEMBER})
        db.put(
            "installs",
            game.id,
            {
                "game": game.to_dict(),
                "executables": [{"name": MEMBER, "executable": inspect_bytes(pe()).to_dict()}],
            },
        )
        with patch("arch_games.catalog.library.visible", return_value=([game], "")):
            library = Library(config, db, profile()).load()
        self.assertEqual(library.games[0].architecture, "unknown")
        self.assertEqual(library.reports[game.id]["status"], "Unknown")
        self.assertEqual(library.reports[game.id]["evidence_scope"], "installer")

    def test_member_not_found_and_invalid_archive_preserve_saved_data(self):
        with self.assertRaises(ArchiveError) as error:
            inspect_archive(self.archive, member="missing.exe")
        self.assertEqual(error.exception.kind, "member_not_found")
        with self.assertRaises(ArchiveError) as error:
            inspect_archive(self.archive, member="../unsafe.exe")
        self.assertEqual(error.exception.kind, "unsafe_archive")
        self.archive.write_bytes(b"Rar!\x1a\x07\x00broken")
        with self.assertRaises(ArchiveError) as error:
            inspect_archive(self.archive)
        self.assertEqual(error.exception.kind, "invalid_archive")
        self.archive.write_bytes(b"not any archive format")
        with self.assertRaises(ArchiveError) as error:
            inspect_archive(self.archive)
        self.assertEqual(error.exception.kind, "unsupported_format")

    def test_worker_exit_signal_timeout_and_protocol_diagnostics(self):
        cases = [
            (-signal.SIGSEGV, "", "", "worker_crash"),
            (-signal.SIGXCPU, "", "", "resource_limit"),
            (-signal.SIGXFSZ, "", "", "resource_limit"),
            (-signal.SIGKILL, "", "", "worker_crash"),
            (1, "", "ModuleNotFoundError: arch_games", "worker_startup"),
            (0, "not JSON", "", "parsing_error"),
            (0, "[]", "", "parsing_error"),
            (1, '{"error":{"kind":[],"message":"invalid protocol"}}', "", "worker_crash"),
            (0, '{"entries": [null]}', "", "parsing_error"),
        ]
        for code, stdout, stderr, kind in cases:
            with (
                self.subTest(kind=kind, code=code),
                patch(
                    "arch_games.compatibility.archives.subprocess.run",
                    return_value=subprocess.CompletedProcess([], code, stdout, stderr),
                ),
                self.assertRaises(ArchiveError) as error,
            ):
                inspect_archive(self.archive)
            self.assertEqual(error.exception.kind, kind)
            self.assertIn("exit status:", error.exception.debug_detail)
        with (
            patch(
                "arch_games.compatibility.archives.subprocess.run",
                side_effect=subprocess.TimeoutExpired([], 33, stderr=b"slow parser"),
            ),
            self.assertRaises(ArchiveError) as error,
        ):
            inspect_archive(self.archive)
        self.assertEqual(error.exception.kind, "timeout")
        self.assertIn("slow parser", error.exception.debug_detail)
        with (
            patch("arch_games.compatibility.archives.subprocess.run", side_effect=FileNotFoundError()),
            self.assertRaises(ArchiveError) as error,
        ):
            inspect_archive(self.archive)
        self.assertEqual(error.exception.kind, "worker_startup")

    def test_worker_exception_protocol_and_debug_redaction(self):
        for failure, kind in [
            (MemoryError(), "resource_limit"),
            (TypeError("synthetic parser problem"), "parsing_error"),
        ]:
            with (
                self.subTest(kind=kind),
                patch("arch_games.compatibility.archive_worker.run", side_effect=failure),
                redirect_stdout(io.StringIO()) as stdout,
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(worker_main(["unused", "", "{}", ""]), 1)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["kind"], kind)
            if kind == "parsing_error":
                self.assertIn("Traceback", stderr.getvalue())
        config = Config(self.root / "state")
        config.save_credentials({"access_token": "fixture-private-token"})
        detail = "Traceback: fixture-private-token\nAuthorization: Bearer other-token\nhttps://user:password@host/a?api_key=example\nparser line 42"
        for debug in [[], ["--debug"]]:
            with (
                patch(
                    "arch_games.cli.analyze_command",
                    side_effect=ArchiveError("parsing_error", "Parser failed", detail),
                ),
                redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(
                    main(["--state-dir", str(self.root / "state"), "analyze", str(self.archive)] + debug), 1
                )
            output = stderr.getvalue()
            self.assertNotIn("fixture-private-token", output)
            self.assertNotIn("other-token", output)
            self.assertNotIn("user:password", output)
            self.assertEqual("parser line 42" in output, bool(debug))
        self.assertNotIn("secret-value", redact_debug("password=secret-value"))

    def test_long_secret_redacted_before_debug_truncation(self):
        secret = "fixture-long-token-" + "a" * 16300
        error = ArchiveError("parsing_error", "Parser failed", "prefix " + secret + " suffix")
        detail = error.diagnostics([secret])
        self.assertNotIn("fixture-long-token-", detail)
        self.assertIn("[REDACTED]", detail)
        self.assertNotIn("\u009b", redact_debug("\u009b31m"))

    def test_installer_feedback_does_not_verify_existing_game_evidence(self):
        from arch_games.compatibility.analyzer import fingerprint

        config = Config(self.root / "state")
        db = Database(config.data_dir / "state.db")
        self.addCleanup(db.close)
        game = Game("gamevault:32", "Example", "gamevault", os="windows")
        db.put(
            "analysis",
            game.id,
            {"executable": inspect_bytes(pe(64)).to_dict(), "requirements": {"graphics": "2d"}},
        )
        feedback = {
            "binary": inspect_bytes(installer_bytes(), name=MEMBER).to_dict(),
            "executable": MEMBER,
            "result": "works",
            "fingerprint": fingerprint(profile()),
        }
        with (
            patch("arch_games.catalog.library.visible", return_value=([game], "")),
            patch("arch_games.compatibility.feedback.current_feedback", return_value=feedback),
        ):
            library = Library(config, db, profile()).load()
        self.assertEqual(library.games[0].architecture, "x86_64")
        self.assertNotEqual(library.reports[game.id]["status"], "Verified Working")

    def test_failed_analysis_does_not_overwrite_saved_evidence(self):
        config = Config(self.root / "state")
        db = Database(config.data_dir / "state.db")
        self.addCleanup(db.close)
        original = {"executable": inspect_bytes(pe(64)).to_dict(), "notes": "Preserve me"}
        db.put("analysis", "gamevault:32", original)
        with redirect_stderr(io.StringIO()):
            code = main(
                [
                    "--state-dir",
                    str(self.root / "state"),
                    "analyze",
                    str(self.archive),
                    "--member",
                    "missing.exe",
                    "--game",
                    "gamevault:32",
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(db.get("analysis", "gamevault:32"), original)
