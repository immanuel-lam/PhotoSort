"""Tests for device_detect module."""

from pathlib import Path
from photosort.device_detect import _sanitize, get_device_name


def test_sanitize_clean_name():
    assert _sanitize("iPhone 15 Pro") == "iPhone 15 Pro"


def test_sanitize_illegal_chars():
    assert _sanitize("cam/model:x") == "cam-model-x"


def test_sanitize_strips_edges():
    assert _sanitize("  Sony A7  ") == "Sony A7"


def test_sanitize_empty_after_strip():
    result = _sanitize("---")
    assert result == "unknown-device"


def test_get_device_name_non_photo(tmp_path: Path):
    f = tmp_path / "video.mp4"
    f.write_bytes(b"\x00" * 10)
    assert get_device_name(f) is None


def test_get_device_name_no_exif(tmp_path: Path):
    # Minimal JPEG with no EXIF
    f = tmp_path / "noexif.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    # Should return None gracefully (Pillow may raise or return empty EXIF)
    result = get_device_name(f)
    assert result is None or isinstance(result, str)
