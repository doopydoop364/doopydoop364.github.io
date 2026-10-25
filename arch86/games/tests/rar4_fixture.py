"""Original, stored-method RAR4 fixture builder. No game code or third-party bytes."""

import binascii
from pathlib import Path
import struct

from test_detection import pe

ROOT = "Example.Game.v1.08.GOG"
MEMBER = ROOT + "/setup_example_game_1.08_(18328).exe"


def block(kind, flags, payload):
    body = struct.pack("<BHH", kind, flags, 7 + len(payload)) + payload
    return struct.pack("<H", binascii.crc32(body) & 0xFFFF) + body


def entry(name, data=b"", directory=False):
    encoded = name.encode("utf-8")
    flags = 0x8000 | (0xE0 if directory else 0)
    header = (
        struct.pack(
            "<IIBIIBBHI",
            len(data),
            len(data),
            2,
            binascii.crc32(data) & 0xFFFFFFFF,
            0,
            20,
            0x30,
            len(encoded),
            0x10 if directory else 0x20,
        )
        + encoded
    )
    return block(0x74, flags, header) + data


def installer_bytes():
    data = bytearray(pe())
    struct.pack_into("<H", data, 88 + 68, 2)  # IMAGE_SUBSYSTEM_WINDOWS_GUI
    return bytes(data) + b"Inno Setup Setup Data (synthetic, nonfunctional)"


def write_rar4(path, unsafe_tail=False, large_prefix=False):
    prefix = [
        (ROOT, b"", True),
        (
            ROOT + "/unrelated.exe",
            b"Not an executable" + (bytes(1024 * 1024) if large_prefix else b""),
            False,
        ),
    ]
    selected = [(MEMBER, installer_bytes(), False)]
    tail = [("../unsafe-after-selected.exe" if unsafe_tail else ROOT + "/other64.exe", pe(64), False)]
    Path(path).write_bytes(
        b"Rar!\x1a\x07\x00"
        + block(0x73, 0, bytes(6))
        + b"".join(entry(name, data, directory) for name, data, directory in prefix + selected + tail)
        + block(0x7B, 0, b"")
    )
    return MEMBER
