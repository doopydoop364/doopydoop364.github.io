from pathlib import Path
import tempfile
import unittest
from arch_games.compatibility.analyzer import analyze, fingerprint
from arch_games.compatibility.feedback import current_feedback, save_feedback
from arch_games.config import AppError
from arch_games.models import Game
from arch_games.storage.database import Database
from test_compatibility import profile
from test_detection import pe


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'state.db')
        self.addCleanup(self.db.close)
        self.path = Path(self.temp.name) / 'game.exe'
        self.path.write_bytes(pe())
        self.profile = profile()
        self.db.put('installs', 'g', {'archive_sha256': 'fixture', 'transaction': 'one'})
        self.db.put('launches', 'l', {'id': 'l', 'game_id': 'g', 'state': 'exited', 'executable': str(self.path),
                                     'fingerprint': fingerprint(self.profile), 'started_at': '2026-10-25'})

    def test_save_update_verify(self):
        record = save_feedback(self.db, 'g', 'works', self.profile, 'User tested')
        self.assertFalse(record['synchronized'])
        result = analyze(Game('g', 'Game', 'gamevault', os='windows'), self.profile, record['binary'], feedback=current_feedback(self.db, 'g'))
        self.assertEqual(result.status, 'Verified Working')
        save_feedback(self.db, 'g', 'untested', self.profile)
        self.assertEqual(len(self.db.all('feedback_history')), 2)

    def test_install_and_binary_change_invalidate(self):
        save_feedback(self.db, 'g', 'works', self.profile)
        self.path.write_bytes(b'changed')
        self.assertIsNone(current_feedback(self.db, 'g'))
        save_feedback(self.db, 'g', 'fails', self.profile)
        self.db.put('installs', 'g', {'archive_sha256': 'new', 'transaction': 'two'})
        self.assertIsNone(current_feedback(self.db, 'g'))

    def test_no_accidental_verification(self):
        with self.assertRaises(AppError):
            save_feedback(self.db, 'unknown', 'works', self.profile)
        self.profile['wine']['version'] = 'changed'
        with self.assertRaises(AppError):
            save_feedback(self.db, 'g', 'works', self.profile)
