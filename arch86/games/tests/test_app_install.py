import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("app_installer", ROOT / "install_app.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AppInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arch games ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.installer = module.ApplicationInstall(self.home, self.home / "data", self.home / "config")

    def test_fresh_upgrade_migration_aliases_remove(self):
        legacy = self.home / ".local/share/arch86-games"
        legacy.mkdir(parents=True)
        (legacy / "catalog.txt").write_text("Custom|custom|Terminal|x|Keep me")
        self.installer.install(ROOT)
        settings = self.installer.config / "settings.json"
        settings.write_text('{"show_all":true}')
        self.installer.install(ROOT)
        self.assertEqual(json.loads(settings.read_text()), {"show_all": True})
        self.assertIn("Custom", (self.installer.config / "catalog.txt").read_text())
        for name in ["arch-games", "arch86-games", "arch64-games"]:
            result = subprocess.run(
                [str(self.installer.bin / name), "--version"], capture_output=True, text=True
            )
            self.assertEqual(result.stdout.strip(), "3.0.0")
        self.installer.uninstall()
        self.assertTrue(settings.is_file())
        self.assertTrue((legacy / "catalog.txt").is_file())
        self.assertFalse((self.installer.bin / "arch-games").exists())

    def test_interrupted_update_recovery(self):
        self.installer.install(ROOT)
        self.installer.app.rename(self.installer.previous)
        stage = self.installer.data / ".app-stage-interrupted"
        stage.mkdir()
        self.installer.journal.write_text(json.dumps({"stage": stage.name}))
        self.installer.recover()
        self.assertTrue(self.installer.app.is_dir())
        self.assertFalse(stage.exists())

    def test_unrelated_launcher_preserved(self):
        self.installer.bin.mkdir(parents=True)
        existing = self.installer.bin / "arch-games"
        existing.write_text("#!/bin/sh\necho unrelated")
        with self.assertRaises(RuntimeError):
            self.installer.install(ROOT)
        self.assertIn("unrelated", existing.read_text())

    def test_piped_bootstrap_does_not_execute_cwd_installer(self):
        import os

        if os.geteuid() == 0:
            self.skipTest("Production bootstrap intentionally refuses root")
        fakebin = self.home / "fakebin"
        fakebin.mkdir()
        fake = fakebin / "python3"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$1"\n')
        fake.chmod(0o700)
        (self.home / "install_app.py").write_text('raise RuntimeError("Never execute this")')
        result = subprocess.run(
            ["sh"],
            input=(ROOT / "install.sh").read_text(),
            cwd=self.home,
            env={**os.environ, "PATH": str(fakebin) + ":" + os.environ["PATH"]},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "-")


class InstalledLayoutTests(AppInstallTests):
    """The installed copy must contain every optional component, and work from the installed launcher."""

    def test_installed_copy_ships_new_components_and_runs(self):
        self.installer.install(ROOT)
        app = self.installer.app
        for rel in ("arch_games/ui/web/static/app.js", "arch_games/ui/web/static/index.html", "arch_games/remote/static/player.js",
                    "arch_games/remote/static/wrapper.html", "host-tools/audio-bridge/make-pyz.sh", "tools/bench_startup.py", "docs/REMOTE_AUDIO_3.md"):
            self.assertTrue((app / rel).is_file(), rel)
        manifest = json.loads((app / ".installed.json").read_text())
        self.assertEqual(manifest["version"], "3.0.0")
        self.assertIn("arch_games/remote/static/player.js", manifest["files"])
        state = self.home / "st"
        for argv in (["--version"], ["--state-dir", str(state), "audio", "devices"], ["--state-dir", str(state), "alias", "list"], ["--state-dir", str(state), "machine", "show"]):
            result = subprocess.run([str(self.installer.bin / "arch-games"), *argv], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, (argv, result.stderr))

    def test_upgrade_from_2_0_launcher_and_data_is_preserved(self):
        self.installer.bin.mkdir(parents=True)
        old = self.installer.bin / "arch-games"
        old.write_text("#!/bin/sh\n# Managed by Arch Games 2.0\nexec python3 -B /old/arch86-games.py \"$@\"\n")
        self.installer.config.mkdir(parents=True, exist_ok=True)
        (self.installer.config / "settings.json").write_text('{"show_all": true, "server_url": "https://gv.example"}')
        self.installer.install(ROOT)
        self.assertIn("Arch Games 3.0", (self.installer.bin / "arch-games").read_text())
        self.assertEqual(json.loads((self.installer.config / "settings.json").read_text())["server_url"], "https://gv.example")
