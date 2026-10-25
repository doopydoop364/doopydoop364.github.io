"""Untrusted archive parsing is isolated with memory/CPU/time limits."""

import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path
import resource
import stat
import sys
import time

from ..config import AppError
from .archives import ArchiveLimits, safe_name
from .executables import MAX_HEADER, inspect_bytes


def run(path, destination, limits):
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.seconds, limits.seconds + 1))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.member_bytes, limits.member_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    library = ctypes.util.find_library("archive")
    if not library:
        raise AppError("Archive operations require system libarchive (Arch: libarchive package)")
    lib = C.CDLL(library)
    signatures = {
        "archive_read_new": (C.c_void_p, []),
        "archive_read_support_filter_all": (C.c_int, [C.c_void_p]),
        "archive_read_support_format_all": (C.c_int, [C.c_void_p]),
        "archive_read_open_filename": (C.c_int, [C.c_void_p, C.c_char_p, C.c_size_t]),
        "archive_read_next_header": (C.c_int, [C.c_void_p, C.POINTER(C.c_void_p)]),
        "archive_read_data": (C.c_ssize_t, [C.c_void_p, C.c_void_p, C.c_size_t]),
        "archive_read_free": (C.c_int, [C.c_void_p]),
        "archive_entry_pathname": (C.c_char_p, [C.c_void_p]),
        "archive_entry_size": (C.c_longlong, [C.c_void_p]),
        "archive_entry_filetype": (C.c_uint, [C.c_void_p]),
        "archive_entry_symlink": (C.c_char_p, [C.c_void_p]),
        "archive_entry_hardlink": (C.c_char_p, [C.c_void_p]),
        "archive_entry_is_encrypted": (C.c_int, [C.c_void_p]),
        "archive_entry_perm": (C.c_uint, [C.c_void_p]),
        "archive_filter_bytes": (C.c_longlong, [C.c_void_p, C.c_int]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = restype, argtypes
    archive = lib.archive_read_new()
    start = time.monotonic()
    records, seen, expanded = [], set(), 0
    archive_size = max(Path(path).stat().st_size, 1)
    try:
        lib.archive_read_support_filter_all(archive)
        lib.archive_read_support_format_all(archive)
        if lib.archive_read_open_filename(archive, os.fsencode(path), 65536) != 0:
            raise AppError("Unsupported, encrypted or corrupted archive")
        entry = C.c_void_p()
        buffer = C.create_string_buffer(65536)
        while True:
            code = lib.archive_read_next_header(archive, C.byref(entry))
            if code == 1:
                break
            if code != 0:
                raise AppError("Corrupt archive entry or unsupported archive feature")
            if time.monotonic() - start > limits.seconds:
                raise AppError("Archive exceeded time limit")
            if len(records) >= limits.files:
                raise AppError("Archive exceeds file-count limit")
            raw_name = lib.archive_entry_pathname(entry)
            if not raw_name:
                raise AppError("Archive entry has no name")
            try:
                name = safe_name(raw_name.decode("utf-8"))
            except UnicodeError:
                raise AppError("Archive filenames must use valid UTF-8") from None
            key = name.casefold()
            if key in seen:
                raise AppError("Archive has duplicate or case-colliding paths")
            seen.add(key)
            kind = lib.archive_entry_filetype(entry)
            if (
                lib.archive_entry_symlink(entry)
                or lib.archive_entry_hardlink(entry)
                or kind not in {stat.S_IFREG, stat.S_IFDIR}
            ):
                raise AppError("Links and special archive files are refused")
            if lib.archive_entry_is_encrypted(entry):
                raise AppError("Encrypted archive entries are not supported")
            size = lib.archive_entry_size(entry)
            if size < 0 or size > limits.member_bytes:
                raise AppError("Archive member exceeds size limit")
            expanded += size
            if expanded > limits.total_bytes or expanded / archive_size > limits.ratio:
                raise AppError("Archive exceeds expanded-size or compression-ratio limit")
            record = {"name": name, "size": size, "directory": kind == stat.S_IFDIR}
            header = bytearray()
            target = Path(destination) / name if destination else None
            if kind == stat.S_IFDIR:
                if target:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                records.append(record)
                continue
            candidate = Path(name).suffix.lower() in {".exe", ".com", ".bin", "", ".x86", ".x86_64"}
            stream = None
            actual = 0
            try:
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    stream = os.fdopen(os.open(target, flags, 0o600), "wb")
                while target or (candidate and len(header) < MAX_HEADER):
                    amount = lib.archive_read_data(
                        archive, buffer, min(65536, MAX_HEADER - len(header)) if not target else 65536
                    )
                    if amount < 0:
                        raise AppError("Archive data is corrupt")
                    if amount == 0:
                        break
                    actual += amount
                    if actual > size or actual > limits.member_bytes:
                        raise AppError("Archive member data exceeds declared limit")
                    if stream:
                        stream.write(buffer.raw[:amount])
                    if candidate and len(header) < MAX_HEADER:
                        header.extend(buffer.raw[: min(amount, MAX_HEADER - len(header))])
                    if time.monotonic() - start > limits.seconds:
                        raise AppError("Archive exceeded time limit")
                if target and actual != size:
                    raise AppError("Archive member size mismatch")
            finally:
                if stream:
                    stream.close()
            if header:
                binary = inspect_bytes(header)
                if binary.format != "unknown":
                    record["executable"] = binary.to_dict()
                    if target and binary.os == "linux":
                        target.chmod(0o700)
            if lib.archive_filter_bytes(archive, 0) > limits.total_bytes:
                raise AppError("Archive decompression exceeded inspection budget")
            records.append(record)
        if not records:
            raise AppError("Archive contains no entries")
        return {
            "entries": records,
            "expanded_bytes": expanded,
            "source": "Local archive headers (bounded libarchive)",
            "nested_archives": "Listed only; never recursively expanded",
        }
    finally:
        lib.archive_read_free(archive)


if __name__ == "__main__":
    try:
        result = run(sys.argv[1], sys.argv[2] or None, ArchiveLimits(**json.loads(sys.argv[3])))
        output = json.dumps(result)
        if len(output) > 16 * 1024 * 1024:
            raise AppError("Archive listing exceeds metadata output limit")
        print(output)
    except (AppError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc) if isinstance(exc, AppError) else "Archive operation failed"}))
        raise SystemExit(1)
