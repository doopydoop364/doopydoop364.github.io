import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from arch_games.compatibility.archives import ArchiveLimits, inspect_archive, extract_archive, safe_name
from arch_games.config import AppError
from test_detection import pe


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_formats(self):
        for extension, mode in [("tar", "w"), ("tar.gz", "w:gz")]:
            path = self.root / ("game." + extension)
            with tarfile.open(path, mode) as archive:
                info = tarfile.TarInfo("Game/Game.exe")
                info.size = len(pe())
                archive.addfile(info, io.BytesIO(pe()))
            self.assertEqual(inspect_archive(path)["entries"][0]["executable"]["format"], "PE32")
        path = self.root / "game.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Game.exe", pe())
            archive.writestr("inner.zip", b"nested archive not expanded")
        report = inspect_archive(path)
        self.assertEqual(report["entries"][0]["executable"]["format"], "PE32")
        dest = self.root / "extract"
        dest.mkdir()
        extract_archive(path, dest)
        self.assertEqual((dest / "Game.exe").read_bytes(), pe())

    def test_optional_binary_fixtures(self):
        for extension in ["7z", "rar"]:
            path = Path(__file__).parents[1] / "fixtures" / ("synthetic." + extension)
            self.assertTrue(path.is_file())
            self.assertEqual(inspect_archive(path)["entries"][0]["name"], "hello.txt")

    def test_paths(self):
        for name in ["../bad", "/absolute", "C:\\bad", "a/../../x", "x\x00y", "a/./b"]:
            with self.subTest(name=name), self.assertRaises(AppError):
                safe_name(name)

    def test_symlink_duplicate_bomb_and_corruption(self):
        path = self.root / "malicious.zip"
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, "../../outside")
        with self.assertRaises(AppError):
            inspect_archive(path)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a", "A")
            archive.writestr("A", "B")
        with self.assertRaises(AppError):
            inspect_archive(path)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("huge", bytes(1024 * 1024))
        with self.assertRaises(AppError):
            inspect_archive(path, ArchiveLimits(total_bytes=1024))
        path.write_bytes(b"broken archive")
        with self.assertRaises(AppError):
            inspect_archive(path)

    def test_count_and_traversal(self):
        path = self.root / "bad.tar"
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("../outside")
            archive.addfile(info, io.BytesIO())
        with self.assertRaises(AppError):
            inspect_archive(path)
        with zipfile.ZipFile(self.root / "count.zip", "w") as archive:
            archive.writestr("a", "a")
            archive.writestr("b", "b")
        with self.assertRaises(AppError):
            inspect_archive(self.root / "count.zip", ArchiveLimits(files=1))
