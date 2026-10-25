import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('app_installer', ROOT / 'install_app.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AppInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='arch games ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.installer = module.ApplicationInstall(self.home, self.home / 'data', self.home / 'config')

    def test_fresh_upgrade_migration_aliases_remove(self):
        legacy = self.home / '.local/share/arch86-games'
        legacy.mkdir(parents=True)
        (legacy / 'catalog.txt').write_text('Custom|custom|Terminal|x|Keep me')
        self.installer.install(ROOT)
        settings = self.installer.config / 'settings.json'
        settings.write_text('{"show_all":true}')
        self.installer.install(ROOT)
        self.assertEqual(json.loads(settings.read_text()), {'show_all': True})
        self.assertIn('Custom', (self.installer.config / 'catalog.txt').read_text())
        for name in ['arch-games', 'arch86-games', 'arch64-games']:
            result = subprocess.run([str(self.installer.bin / name), '--version'], capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), '2.0.0')
        self.installer.uninstall()
        self.assertTrue(settings.is_file())
        self.assertTrue((legacy / 'catalog.txt').is_file())
        self.assertFalse((self.installer.bin / 'arch-games').exists())

    def test_interrupted_update_recovery(self):
        self.installer.install(ROOT)
        self.installer.app.rename(self.installer.previous)
        stage = self.installer.data / '.app-stage-interrupted'
        stage.mkdir()
        self.installer.journal.write_text(json.dumps({'stage': stage.name}))
        self.installer.recover()
        self.assertTrue(self.installer.app.is_dir())
        self.assertFalse(stage.exists())

    def test_unrelated_launcher_preserved(self):
        self.installer.bin.mkdir(parents=True)
        existing = self.installer.bin / 'arch-games'
        existing.write_text('#!/bin/sh\necho unrelated')
        with self.assertRaises(RuntimeError):
            self.installer.install(ROOT)
        self.assertIn('unrelated', existing.read_text())
