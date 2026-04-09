"""Tests for hashing module."""

from pathlib import Path
from photosort.hashing import sha256_file


def test_hash_consistent(tmp_path: Path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"hello world")
    assert sha256_file(f) == sha256_file(f)


def test_hash_differs_for_different_content(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"aaaa")
    b.write_bytes(b"bbbb")
    assert sha256_file(a) != sha256_file(b)


def test_hash_same_content_different_name(tmp_path: Path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    assert sha256_file(a) == sha256_file(b)


def test_hash_empty_file(tmp_path: Path):
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    # SHA256 of empty string is well-known
    assert sha256_file(f) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
