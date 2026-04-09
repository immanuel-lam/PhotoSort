"""Shared pytest fixtures."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_photos(tmp_path: Path) -> Path:
    """Return a temp directory pre-populated with dummy media files."""
    src = tmp_path / "source"
    src.mkdir()
    # Create minimal JPEG stubs (not valid EXIF, but valid for path-based tests)
    (src / "IMG_20240715_083012.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
    (src / "VID_20230101_120000.mp4").write_bytes(b"\x00" * 10)
    (src / "Screenshot_20240101-090000_Chrome.jpg").write_bytes(b"\xff\xd8\xff")
    (src / "random.txt").write_text("not a media file")
    return src
