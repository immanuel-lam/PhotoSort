"""Tests for the core sorting engine."""

from pathlib import Path
import pytest

from photosort.engine import (
    scan_media_files,
    compute_destination,
    sort_files,
    MISC_FOLDER,
)
from photosort.models import DateSource, SortConfig


# ── scan_media_files ──────────────────────────────────────────────────────────

def test_scan_finds_media(tmp_photos: Path):
    files = scan_media_files(tmp_photos)
    names = [f.name for f in files]
    assert "IMG_20240715_083012.jpg" in names
    assert "VID_20230101_120000.mp4" in names
    assert "random.txt" not in names


def test_scan_recursive(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "IMG_20240101_000000.jpg").touch()
    files = scan_media_files(tmp_path)
    assert len(files) == 1


def test_scan_empty_dir(tmp_path: Path):
    assert scan_media_files(tmp_path) == []


# ── compute_destination ───────────────────────────────────────────────────────

def test_compute_destination_with_device(tmp_path: Path):
    from datetime import datetime
    f = tmp_path / "IMG.jpg"
    f.touch()
    dt = datetime(2024, 7, 15)
    dest = compute_destination(f, dt, "ILCE-7M3", "%Y/%Y-%m/%Y-%m-%d", tmp_path / "out")
    assert dest == tmp_path / "out" / "ILCE-7M3" / "2024" / "2024-07" / "2024-07-15" / "IMG.jpg"


def test_compute_destination_misc(tmp_path: Path):
    from datetime import datetime
    f = tmp_path / "IMG.jpg"
    f.touch()
    dt = datetime(2024, 1, 1)
    dest = compute_destination(f, dt, None, "%Y/%Y-%m/%Y-%m-%d", tmp_path / "out")
    assert dest.parts[-5] == MISC_FOLDER


# ── sort_files — dry run ──────────────────────────────────────────────────────

def test_dry_run_moves_nothing(tmp_photos: Path, tmp_path: Path):
    out = tmp_path / "out"
    config = SortConfig(source=tmp_photos, destination=out, dry_run=True)
    result = sort_files(config)
    # Nothing should have been created
    assert not out.exists()
    # But stats should reflect the found files
    assert result.total_files >= 2


def test_dry_run_result_counts(tmp_photos: Path, tmp_path: Path):
    out = tmp_path / "out"
    config = SortConfig(source=tmp_photos, destination=out, dry_run=True)
    result = sort_files(config)
    assert result.errors == 0
    assert result.moved + result.duplicates == result.total_files


# ── sort_files — live run ─────────────────────────────────────────────────────

def test_live_run_moves_files(tmp_photos: Path, tmp_path: Path):
    out = tmp_path / "out"
    config = SortConfig(source=tmp_photos, destination=out, dry_run=False)
    result = sort_files(config)
    assert result.errors == 0
    # Source media files should be gone
    remaining = list(tmp_photos.rglob("*.jpg")) + list(tmp_photos.rglob("*.mp4"))
    assert remaining == []
    # Output folder should have files
    moved_files = list(out.rglob("*"))
    assert any(f.is_file() for f in moved_files)


def test_live_run_1to1(tmp_photos: Path, tmp_path: Path):
    out = tmp_path / "out"
    config = SortConfig(source=tmp_photos, destination=out, dry_run=False)
    result = sort_files(config)
    from photosort.constants import ALL_EXTENSIONS
    output_files = [f for f in out.rglob("*") if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS]
    assert len(output_files) == result.total_files


# ── sort_files — duplicates ───────────────────────────────────────────────────

def test_duplicates_routed_to_dup_folder(tmp_path: Path):
    """
    Duplicate detection is filename-collision-based (lazy hashing).
    Two files with the exact same name that resolve to the same destination
    and identical content → second goes to duplicates/Dn/.
    """
    src = tmp_path / "src"
    sub1 = src / "a"
    sub2 = src / "b"
    sub1.mkdir(parents=True)
    sub2.mkdir(parents=True)
    out = tmp_path / "out"

    content = b"\xff\xd8\xff" + b"\xAB" * 100
    # Same filename, same date embedded, same content → filename collision at destination
    (sub1 / "IMG_20240715_083012.jpg").write_bytes(content)
    (sub2 / "IMG_20240715_083012.jpg").write_bytes(content)

    config = SortConfig(source=src, destination=out, dry_run=False)
    result = sort_files(config)

    dup_records = [r for r in result.records if r.is_duplicate]
    assert len(dup_records) >= 1
    # Duplicate path should contain 'duplicates'
    assert all("duplicates" in str(r.dest_path) for r in dup_records)


# ── sort_files — priority ─────────────────────────────────────────────────────

def test_priority_modified_first(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    f = src / "IMG_20240715_083012.jpg"
    f.touch()
    config = SortConfig(
        source=src,
        destination=out,
        dry_run=True,
        priority=[DateSource.MODIFIED, DateSource.FILENAME],
    )
    result = sort_files(config)
    assert result.records[0].date_source == DateSource.MODIFIED


# ── on_progress callback ──────────────────────────────────────────────────────

def test_progress_callback_called(tmp_photos: Path, tmp_path: Path):
    calls = []
    def cb(current, total, record):
        calls.append((current, total))

    config = SortConfig(source=tmp_photos, destination=tmp_path / "out", dry_run=True)
    result = sort_files(config, on_progress=cb)
    assert len(calls) == result.total_files
    # current should go 1..n
    assert calls[0][0] == 1
    assert calls[-1][0] == result.total_files
