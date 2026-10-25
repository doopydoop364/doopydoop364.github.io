import struct
from types import SimpleNamespace
import unittest
from arch_games.compatibility.executables import inspect_bytes
from arch_games.compatibility.graphics import detect_graphics
from arch_games.compatibility.wine import detect_wine


def elf(bits=32, machine=3):
    data = bytearray(64)
    data[:6] = b"\x7fELF" + bytes([1 if bits == 32 else 2, 1])
    struct.pack_into("<H", data, 18, machine)
    return bytes(data)


def pe(bits=32, machine=None):
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<HH", data, 68, machine or (0x14C if bits == 32 else 0x8664), 0)
    struct.pack_into("<H", data, 84, 224 if bits == 32 else 240)
    struct.pack_into("<H", data, 88, 0x10B if bits == 32 else 0x20B)
    return bytes(data)


class DetectionTests(unittest.TestCase):
    def test_binary_headers(self):
        for data, form, arch in [
            (elf(), "ELF32", "i686"),
            (elf(64, 62), "ELF64", "x86_64"),
            (pe(), "PE32", "i686"),
            (pe(64), "PE32+", "x86_64"),
            (b"MZ" + bytes(62), "DOS", "i686"),
        ]:
            with self.subTest(form=form):
                result = inspect_bytes(data)
                self.assertEqual((result.format, result.architecture), (form, arch))

    def test_truncated_and_foreign(self):
        self.assertEqual(inspect_bytes(b"\x7fELF").format, "unknown")
        self.assertEqual(inspect_bytes(pe()[:100]).format, "unknown")
        self.assertEqual(inspect_bytes(elf(64, 183)).architecture, "aarch64")
        self.assertEqual(inspect_bytes(pe(64, 0x14C)).format, "unknown")

    def test_graphics_software(self):
        def run(args, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="OpenGL renderer string: llvmpipe\nAccelerated: no" if args[0] == "glxinfo" else "",
            )

        result = detect_graphics(run, {"DISPLAY": ":0"})
        self.assertFalse(result["accelerated"])
        self.assertIsNone(result["vulkan"])

    def test_missing_tools(self):
        def run(*args, **kwargs):
            raise FileNotFoundError()

        result = detect_graphics(run, {})
        self.assertFalse(result["display"])
        self.assertIsNone(result["accelerated"])
        self.assertFalse(detect_wine(run, lambda name: None)["available"])

    def test_pe_imports_and_foreign_abi(self):
        data = bytearray(pe()) + bytearray(512)
        struct.pack_into("<H", data, 70, 1)
        struct.pack_into("<II", data, 192, 0x1000, 60)
        struct.pack_into("<IIII", data, 320, 512, 0x1000, 512, 512)
        struct.pack_into("<I", data, 524, 0x1080)
        struct.pack_into("<I", data, 544, 0x1090)
        data[640:650] = b"d3d11.dll\0"
        data[656:669] = b"msvcp140.dll\0"
        detected = inspect_bytes(data)
        self.assertEqual(detected.imports, ["d3d11.dll", "msvcp140.dll"])
        foreign = bytearray(elf(64, 62))
        foreign[7] = 9
        self.assertEqual(inspect_bytes(foreign).os, "freebsd")
        self.assertEqual(inspect_bytes(elf(32, 62)).format, "unknown")
