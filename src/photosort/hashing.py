"""SHA256 content hashing for duplicate detection."""

import hashlib
from pathlib import Path

_CHUNK_SIZE = 65536  # 64 KB


def sha256_file(filepath: Path) -> str:
    """Return the hex SHA256 digest of a file's contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()
