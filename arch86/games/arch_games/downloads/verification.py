"""Streaming digests; size checks are not cryptographic verification."""

import hashlib
from pathlib import Path
from ..config import AppError


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path, expected_size=None, checksum=None):
    if expected_size is not None and Path(path).stat().st_size != expected_size:
        raise AppError("Downloaded file size mismatch")
    digest = sha256(path)
    if checksum and digest.casefold() != checksum.casefold():
        raise AppError("Downloaded file checksum mismatch")
    return {
        "sha256": digest,
        "verification": "sha256-verified"
        if checksum
        else "size-only"
        if expected_size is not None
        else "unverified",
    }
