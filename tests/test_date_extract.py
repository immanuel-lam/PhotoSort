"""Tests for date_extract module."""

from datetime import datetime
from pathlib import Path

import pytest

from photosort.date_extract import get_filename_date, get_modified_date, get_best_date
from photosort.models import DateSource


@pytest.mark.parametrize("filename,expected", [
    ("IMG_20240715_083012.jpg",           datetime(2024, 7, 15, 8, 30, 12)),
    ("VID_20230101_120000.mp4",           datetime(2023, 1, 1, 12, 0, 0)),
    ("PXL_20240715_083012345.jpg",        datetime(2024, 7, 15, 8, 30, 12)),
    ("Screenshot_20240715-083012.jpg",    datetime(2024, 7, 15, 8, 30, 12)),
    ("Screenshot_2024-07-15-08-30-12.jpg", datetime(2024, 7, 15, 8, 30, 12)),
    ("20240715_083012.jpg",               datetime(2024, 7, 15, 8, 30, 12)),
])
def test_filename_date_patterns(filename: str, expected: datetime, tmp_path: Path):
    f = tmp_path / filename
    f.touch()
    result = get_filename_date(f)
    assert result == expected


def test_filename_date_no_match(tmp_path: Path):
    f = tmp_path / "random_file.jpg"
    f.touch()
    assert get_filename_date(f) is None


def test_modified_date(tmp_path: Path):
    f = tmp_path / "test.jpg"
    f.touch()
    result = get_modified_date(f)
    assert isinstance(result, datetime)


def test_get_best_date_uses_priority(tmp_path: Path):
    # File with a recognizable filename pattern but no EXIF
    f = tmp_path / "IMG_20240715_083012.jpg"
    f.touch()
    priority = [DateSource.EXIF, DateSource.FILENAME, DateSource.MODIFIED]
    dt, source = get_best_date(f, priority)
    # EXIF will fail (no real EXIF), filename should match
    assert source == DateSource.FILENAME
    assert dt == datetime(2024, 7, 15, 8, 30, 12)


def test_get_best_date_fallback_to_modified(tmp_path: Path):
    f = tmp_path / "nopattern.jpg"
    f.touch()
    priority = [DateSource.EXIF, DateSource.FILENAME, DateSource.MODIFIED]
    dt, source = get_best_date(f, priority)
    assert source == DateSource.MODIFIED
    assert isinstance(dt, datetime)


def test_get_best_date_respects_order(tmp_path: Path):
    # Put MODIFIED first — should use mtime even though FILENAME would also match
    f = tmp_path / "IMG_20240715_083012.jpg"
    f.touch()
    priority = [DateSource.MODIFIED, DateSource.FILENAME]
    dt, source = get_best_date(f, priority)
    assert source == DateSource.MODIFIED
