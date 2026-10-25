"""Bounded ELF, PE and DOS header inspection. Never loads or executes a target."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
import struct
import re

MAX_HEADER = 1024 * 1024


@dataclass
class Executable:
    format: str = "unknown"
    architecture: str = "unknown"
    bits: int = 0
    os: str = "unknown"
    imports: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    role: str = "unknown"
    subsystem: str = "unknown"

    def to_dict(self):
        return asdict(self)


def is_installer(binary, name=None):
    if not binary:
        return False
    if binary.get("role") == "installer":
        return True
    basename = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return binary.get("os") == "windows" and bool(
        re.match(r"^(?:setup|install(?:er)?)(?:[_. -]|\.exe$|$)", basename, re.I)
    )


def label_executable(binary, name=None):
    value = dict(binary)
    if is_installer(value, name):
        value["role"] = "installer"
        value["evidence"] = list(value.get("evidence", []))
        note = "Installer evidence only; installed game architecture is not established"
        if note not in value["evidence"]:
            value["evidence"].append(note)
    return value


def inspect_bytes(data, name=None):
    data = data[:MAX_HEADER]
    result = Executable()
    if data.startswith(b"\x7fELF"):
        if len(data) < 52 or data[4] not in {1, 2} or data[5] not in {1, 2}:
            return result
        endian = "<" if data[5] == 1 else ">"
        machine = struct.unpack_from(endian + "H", data, 18)[0]
        bits = 32 if data[4] == 1 else 64
        if bits == 64 and len(data) < 64:
            return result
        arch = {3: "i686", 62: "x86_64", 40: "arm", 183: "aarch64"}.get(machine, "unknown")
        if (arch == "i686" and bits != 32) or (arch in {"x86_64", "aarch64"} and bits != 64):
            return result
        abi = {0: "linux", 3: "linux", 2: "netbsd", 6: "solaris", 9: "freebsd"}.get(data[7], "unknown")
        return Executable(
            "ELF" + str(bits),
            arch,
            bits,
            abi,
            evidence=[
                "ELF class/machine and OSABI header; System V ABI treated as Linux with library caveats"
            ],
        )
    if not data.startswith(b"MZ") or len(data) < 28:
        return result
    if len(data) >= 64:
        offset = struct.unpack_from("<I", data, 60)[0]
        if offset:
            if offset < 64 or offset + 26 > len(data) or data[offset : offset + 4] != b"PE\0\0":
                return Executable(
                    evidence=["MZ with unrecognized, truncated or out-of-limit secondary header"]
                )
            machine, sections = struct.unpack_from("<HH", data, offset + 4)
            optional_size = struct.unpack_from("<H", data, offset + 20)[0]
            optional = offset + 24
            magic = struct.unpack_from("<H", data, optional)[0]
            bits = {0x10B: 32, 0x20B: 64}.get(magic, 0)
            if (
                not bits
                or optional_size < (96 if bits == 32 else 112)
                or optional + optional_size > len(data)
            ):
                return result
            arch = {0x14C: "i686", 0x8664: "x86_64", 0xAA64: "aarch64"}.get(machine, "unknown")
            if (arch == "i686" and bits != 32) or (arch in {"x86_64", "aarch64"} and bits != 64):
                return result
            imports = _pe_imports(data, optional, optional_size, sections, bits)
            subsystem = {2: "Windows GUI", 3: "Windows console"}.get(
                struct.unpack_from("<H", data, optional + 68)[0], "unknown"
            )
            binary = Executable(
                "PE32" if bits == 32 else "PE32+",
                arch,
                bits,
                "windows",
                imports,
                ["PE signature, optional header and COFF machine"],
                subsystem=subsystem,
            )
            if b"Inno Setup Setup Data" in data:
                binary.evidence.append("Inno Setup Setup Data marker present")
            if is_installer(binary.to_dict(), name) or b"Inno Setup Setup Data" in data:
                binary.role = "installer"
                binary.evidence.append(
                    "Installer filename or bounded Inno Setup marker hint; payload not inspected"
                )
            return Executable(**label_executable(binary.to_dict(), name))
    return Executable("DOS", "i686", 16, "dos", evidence=["MZ DOS header; no recognized PE secondary header"])


def _pe_imports(data, optional, size, count, bits):
    """Read import names only when all tables lie inside the inspection budget."""
    if count > 96:
        return []
    directory = optional + (96 if bits == 32 else 112)
    if directory + 16 > optional + size:
        return []
    rva, _ = struct.unpack_from("<II", data, directory + 8)
    table = optional + size
    sections = []
    for i in range(count):
        pos = table + 40 * i
        if pos + 40 > len(data):
            return []
        virtual_size, va, raw_size, raw = struct.unpack_from("<IIII", data, pos + 8)
        sections.append((va, raw_size, raw))

    def at(address):
        for va, length, raw in sections:
            if va <= address < va + length:
                offset = raw + address - va
                return offset if offset < len(data) else None
        return None

    start = at(rva)
    if start is None:
        return []
    imports = []
    for i in range(128):
        pos = start + 20 * i
        if pos + 20 > len(data) or not any(data[pos : pos + 20]):
            break
        name = at(struct.unpack_from("<I", data, pos + 12)[0])
        if name is None:
            continue
        end = data.find(b"\0", name, min(len(data), name + 256))
        if end != -1:
            text = data[name:end].decode("ascii", "replace").lower()
            if text.endswith(".dll") and all(32 <= ord(c) < 127 for c in text):
                imports.append(text)
    return sorted(set(imports))


def inspect_file(path):
    path = Path(path)
    if not path.is_file():
        return Executable(evidence=["Not a regular file"])
    with path.open("rb") as stream:
        return inspect_bytes(stream.read(MAX_HEADER), name=path.name)
