"""Untrusted archive parsing is isolated with memory/CPU/time limits."""

import ctypes as C
import ctypes.util
import errno
import json
import os
from pathlib import Path
import resource
import stat
import sys
import time
import traceback

from .archive_errors import ArchiveError
from .archives import ArchiveLimits, safe_name
from .executables import MAX_HEADER, inspect_bytes


def run(path, destination, limits, member=None):
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.seconds, limits.seconds + 1))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.member_bytes, limits.member_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    library = ctypes.util.find_library("archive")
    if not library:
        raise ArchiveError(
            "unsupported_format", "System libarchive is unavailable (Arch: libarchive package)"
        )
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
        "archive_filter_bytes": (C.c_longlong, [C.c_void_p, C.c_int]),
        "archive_error_string": (C.c_char_p, [C.c_void_p]),
        "archive_errno": (C.c_int, [C.c_void_p]),
    }
    for name, (restype, argtypes) in signatures.items():
        function = getattr(lib, name)
        function.restype, function.argtypes = restype, argtypes
    archive = lib.archive_read_new()
    if not archive:
        raise MemoryError("libarchive allocation failed")
    start = time.monotonic()
    records, seen, expanded, scanned = [], set(), 0, 0
    archive_size = max(Path(path).stat().st_size, 1)
    with Path(path).open("rb") as stream:
        magic = stream.read(512)
    known_magic = (
        magic.startswith((b"Rar!", b"PK", b"7z\xbc\xaf\x27\x1c", b"\x1f\x8b")) or magic[257:262] == b"ustar"
    )

    def native_failure(stage):
        raw = lib.archive_error_string(archive)
        detail = raw.decode("utf-8", "replace")[:2048] if raw else "No libarchive error string"
        error_number = lib.archive_errno(archive)
        lower = detail.lower()
        if error_number == errno.ENOMEM or "allocate memory" in lower or "out of memory" in lower:
            kind = "resource_limit"
        elif (
            "unsupported" in lower
            or "not supported" in lower
            or "unrecognized archive format" in lower
            and not known_magic
        ):
            kind = "unsupported_format"
        else:
            kind = "invalid_archive"
        raise ArchiveError(
            kind, "libarchive failed during " + stage, "{}: {} (errno {})".format(stage, detail, error_number)
        )

    try:
        lib.archive_read_support_filter_all(archive)
        lib.archive_read_support_format_all(archive)
        if lib.archive_read_open_filename(archive, os.fsencode(path), 65536) != 0:
            native_failure("archive open")
        entry = C.c_void_p()
        buffer = C.create_string_buffer(65536)
        while True:
            code = lib.archive_read_next_header(archive, C.byref(entry))
            if code == 1:
                break
            if code != 0:
                native_failure("entry header")
            if time.monotonic() - start > limits.seconds:
                raise ArchiveError("timeout", "Archive exceeded inspection deadline")
            if lib.archive_filter_bytes(archive, 0) > limits.total_bytes:
                raise ArchiveError("resource_limit", "Archive decompression exceeded inspection budget")
            scanned += 1
            if scanned > limits.files:
                raise ArchiveError("resource_limit", "Archive exceeds file-count limit")
            raw_name = lib.archive_entry_pathname(entry)
            if not raw_name:
                raise ArchiveError("invalid_archive", "Archive entry has no name")
            try:
                name = safe_name(raw_name.decode("utf-8"))
            except UnicodeError:
                raise ArchiveError("invalid_archive", "Archive filenames must use valid UTF-8") from None
            key = name.casefold()
            if key in seen:
                raise ArchiveError("unsafe_archive", "Archive has duplicate or case-colliding paths")
            seen.add(key)
            # Sequential formats require headers preceding the target, but no payload
            # inspection for them. Stop immediately after the selected header prefix.
            if member is not None and name != member:
                continue
            kind = lib.archive_entry_filetype(entry)
            if (
                lib.archive_entry_symlink(entry)
                or lib.archive_entry_hardlink(entry)
                or kind not in {stat.S_IFREG, stat.S_IFDIR}
            ):
                raise ArchiveError("unsafe_archive", "Links and special archive files are refused")
            if lib.archive_entry_is_encrypted(entry):
                raise ArchiveError("unsupported_format", "Encrypted archive entries are not supported")
            size = lib.archive_entry_size(entry)
            if size < 0:
                raise ArchiveError("invalid_archive", "Archive member has an invalid declared size")
            if size > limits.member_bytes:
                raise ArchiveError("resource_limit", "Archive member exceeds size limit")
            expanded += size
            if expanded > limits.total_bytes or expanded / archive_size > limits.ratio:
                raise ArchiveError(
                    "resource_limit", "Archive exceeds expanded-size or compression-ratio limit"
                )
            record = {"name": name, "size": size, "directory": kind == stat.S_IFDIR}
            header = bytearray()
            target = Path(destination) / name if destination else None
            if kind == stat.S_IFDIR:
                if member is not None:
                    raise ArchiveError(
                        "member_not_found", "Selected member is a directory, not an executable"
                    )
                if target:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                records.append(record)
                continue
            candidate = member is not None or Path(name).suffix.lower() in {
                ".exe",
                ".com",
                ".bin",
                "",
                ".x86",
                ".x86_64",
            }
            stream = None
            actual = 0
            try:
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    stream = os.fdopen(os.open(target, flags, 0o600), "wb")
                while target or candidate and len(header) < MAX_HEADER:
                    amount = lib.archive_read_data(
                        archive, buffer, min(65536, MAX_HEADER - len(header)) if not target else 65536
                    )
                    if amount < 0:
                        native_failure("member data")
                    if amount == 0:
                        break
                    actual += amount
                    if actual > size or actual > limits.member_bytes:
                        raise ArchiveError("invalid_archive", "Archive member data exceeds declared size")
                    if stream:
                        stream.write(buffer.raw[:amount])
                    if candidate and len(header) < MAX_HEADER:
                        header.extend(buffer.raw[: min(amount, MAX_HEADER - len(header))])
                    if time.monotonic() - start > limits.seconds:
                        raise ArchiveError("timeout", "Archive exceeded inspection deadline")
                if target and actual != size:
                    raise ArchiveError("invalid_archive", "Archive member size mismatch")
            finally:
                if stream:
                    stream.close()
            record["header_bytes_read"] = len(header)
            if header:
                binary = inspect_bytes(header, name=name)
                if binary.format != "unknown":
                    record["executable"] = binary.to_dict()
                    if target and binary.os == "linux":
                        target.chmod(0o700)
            if lib.archive_filter_bytes(archive, 0) > limits.total_bytes:
                raise ArchiveError("resource_limit", "Archive decompression exceeded inspection budget")
            records.append(record)
            if member is not None:
                break
        if not records:
            if member is not None:
                raise ArchiveError("member_not_found", "Selected member was not found")
            raise ArchiveError("invalid_archive", "Archive contains no entries")
        return {
            "entries": records,
            "expanded_bytes": expanded,
            "scanned_entries": scanned,
            "selected_member": member,
            "complete_archive_validation": False,
            "listing_complete": member is None,
            "source": "Selected member header (bounded libarchive)"
            if member
            else "Local archive headers (bounded libarchive)",
            "nested_archives": "Listed only; never recursively expanded",
        }
    finally:
        lib.archive_read_free(archive)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        result = run(argv[0], argv[1] or None, ArchiveLimits(**json.loads(argv[2])), argv[3] or None)
        output = json.dumps(result)
        if len(output) > 16 * 1024 * 1024:
            raise ArchiveError("resource_limit", "Archive listing exceeds metadata output limit")
        print(output)
        return 0
    except ArchiveError as exc:
        error = {"kind": exc.kind, "message": str(exc).split(": ", 1)[1], "detail": exc.debug_detail}
    except MemoryError:
        error = {"kind": "resource_limit", "message": "Archive worker exhausted its address-space budget"}
    except OSError as exc:
        error = {
            "kind": "resource_limit" if exc.errno == errno.ENOMEM else "io_error",
            "message": "Archive worker could not access its input or runtime",
        }
        traceback.print_exc(file=sys.stderr)
    except Exception as exc:
        error = {"kind": "parsing_error", "message": "Archive parser raised " + type(exc).__name__}
        traceback.print_exc(file=sys.stderr)
    print(json.dumps({"error": error}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
