"""Minimal RFC 6455 server side over asyncio streams (no fragmentation, bounded sizes)."""

import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 1, 2, 8, 9, 10


class ProtocolError(Exception):
    pass


def accept_key(key):
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def encode_frame(opcode, payload=b""):
    if isinstance(payload, str):
        payload = payload.encode()
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([n])
    elif n < 65536:
        header += bytes([126]) + struct.pack(">H", n)
    else:
        header += bytes([127]) + struct.pack(">Q", n)
    return header + payload


async def read_frame(reader, max_payload=65536):
    """Return (opcode, payload). Client frames must be masked, final and within max_payload."""
    head = await reader.readexactly(2)
    fin, opcode = head[0] & 0x80, head[0] & 0x0F
    masked, n = head[1] & 0x80, head[1] & 0x7F
    if head[0] & 0x70 or not fin:
        raise ProtocolError("fragmented or extension frames are not supported")
    if not masked:
        raise ProtocolError("client frames must be masked")
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    if n > max_payload or (opcode >= 8 and n > 125):
        raise ProtocolError("frame too large")
    mask = await reader.readexactly(4)
    data = bytearray(await reader.readexactly(n))
    for i in range(n):
        data[i] ^= mask[i % 4]
    return opcode, bytes(data)


def client_frame(opcode, payload=b"", mask=b"\x01\x02\x03\x04"):
    """Masked frame, for tests and the in-repo status client."""
    if isinstance(payload, str):
        payload = payload.encode()
    n = len(payload)
    header = bytes([0x80 | opcode])
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
