import struct
import tempfile
import unittest
from pathlib import Path

from arch_games.config import Config
from arch_games.runtime.discovery import discover
from arch_games.runtime.shortcuts import desktop_entries_for_prefix, parse_lnk, prefix_shortcuts
from arch_games.storage.database import Database
from test_detection import pe
from test_discovery_launch import GAME, build_undertale


def make_lnk(target, working_dir=None, arguments=None, unicode_strings=True):
    """Shell Link per MS-SHLLINK: header, LinkInfo with LocalBasePath, optional StringData."""
    flags = 0x2  # HasLinkInfo
    if working_dir:
        flags |= 0x10
    if arguments:
        flags |= 0x20
    if unicode_strings:
        flags |= 0x80
    header = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46", flags) + bytes(76 - 24)
    base = target.encode("cp1252") + b"\0"
    volume = struct.pack("<IIII", 16, 3, 0, 16) + b"\0"  # minimal VolumeID
    volume_off = 28
    local_off = volume_off + len(volume)
    info_size = local_off + len(base) + 1
    link_info = struct.pack("<IIIIII", info_size, 28, 0x1, volume_off, local_off, 0) + struct.pack("<I", 0) + volume + base + b"\0"
    link_info = link_info[:info_size].ljust(info_size, b"\0")

    def counted(text):
        raw = text.encode("utf-16-le" if unicode_strings else "cp1252")
        return struct.pack("<H", len(text)) + raw

    tail = (counted(working_dir) if working_dir else b"") + (counted(arguments) if arguments else b"")
    return header + link_info + tail


class LnkTests(unittest.TestCase):
    def test_parse_target_workdir_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            for uni in (True, False):
                path = Path(d) / "g.lnk"
                path.write_bytes(make_lnk("C:\\GOG Games\\Undertale\\UNDERTALE.exe", "C:\\GOG Games\\Undertale", "-w", uni))
                parsed = parse_lnk(path)
                self.assertEqual(parsed["target"], "C:\\GOG Games\\Undertale\\UNDERTALE.exe")
                self.assertEqual(parsed["working_dir"], "C:\\GOG Games\\Undertale")
                self.assertEqual(parsed["arguments"], "-w")

    def test_trailing_nul_in_strings_as_written_by_wine(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "w.lnk"
            path.write_bytes(make_lnk("C:\\G\\g.exe", "C:\\G\\\x00", "\x00"))
            parsed = parse_lnk(path)
            self.assertEqual((parsed["working_dir"], parsed["arguments"]), ("C:\\G\\", ""))

    def test_garbage_truncated_and_oversized_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            good = make_lnk("C:\\x.exe")
            for name, data in {"empty": b"", "junk": b"nope" * 50, "trunc": good[:90], "huge": good + bytes(70000)}.items():
                p = Path(d) / (name + ".lnk")
                p.write_bytes(data)
                self.assertIsNone(parse_lnk(p), name)
            link = Path(d) / "link.lnk"
            link.symlink_to(Path(d) / "empty.lnk")
            self.assertIsNone(parse_lnk(link))

    def test_prefix_shortcuts_and_desktop_entries(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / "prefix"
            menu = prefix / "drive_c/users/player/Start Menu/Programs/GOG.com/Undertale"
            menu.mkdir(parents=True)
            (menu / "Undertale.lnk").write_bytes(make_lnk("C:\\GOG Games\\Undertale\\UNDERTALE.exe"))
            outside = Path(d) / "outside"
            outside.mkdir()
            (outside / "Evil.lnk").write_bytes(make_lnk("C:\\evil.exe"))
            (prefix / "drive_c/users/player/Desktop").symlink_to(outside)  # symlinked Desktop is never followed
            found = prefix_shortcuts(prefix)
            self.assertEqual([v["target"] for v in found.values()], ["C:\\GOG Games\\Undertale\\UNDERTALE.exe"])
            apps = Path(d) / "applications"
            apps.mkdir()
            (apps / "Undertale.desktop").write_text('[Desktop Entry]\nName=Undertale\nExec=env WINEPREFIX="{}" wine start /ProgramIDs x /Unix "{}"\n'.format(prefix, menu / "Undertale.lnk"))
            (apps / "other.desktop").write_text('[Desktop Entry]\nName=Other\nExec=env WINEPREFIX="/elsewhere" wine start /Unix "/x.lnk"\n')
            entries = desktop_entries_for_prefix(prefix, apps)
            self.assertEqual([e["name"] for e in entries], ["Undertale"])
            self.assertEqual(entries[0]["lnk"], str(menu / "Undertale.lnk"))


class DiscoveryShortcutTests(unittest.TestCase):
    def test_shortcut_target_outranks_name_only_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            config = Config(Path(d) / "s")
            db = Database(config.data_dir / "state.db")
            self.addCleanup(db.close)
            prefix, game = build_undertale(config, db)
            (game / "goggame-1207659032.info").unlink()  # no GOG metadata: rely on other evidence
            (game / "Launcher2.exe").write_bytes(pe(32) + bytes(2_000_000))
            (game / "UNDERTALE.exe").rename(game / "Game.exe")
            menu = prefix / "drive_c/users/player/Start Menu/Programs"
            menu.mkdir(parents=True)
            (menu / "Undertale.lnk").write_bytes(make_lnk("C:\\GOG Games\\Undertale\\Launcher2.exe"))
            found = discover(config, db, GAME, "Undertale")
            self.assertEqual(found[0]["name"], "Launcher2.exe")
            self.assertTrue(any("shortcut points to it" in e for e in found[0]["evidence"]))
