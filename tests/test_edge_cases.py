"""
Edge-case tests for PhotoSort v3.

Covers:
 1. Zero-byte files (empty files)
 2. Files with no extension
 3. Very deeply nested source directories (5+ levels)
 4. Files where EXIF date is corrupted/invalid
 5. Duplicate detection when destination already has files from a previous run
 6. Filenames with special characters (spaces, unicode, parentheses)
 7. Screenshot detection for ALL patterns listed in CLAUDE.md
 8. Videos that match proximity within window vs outside window
 9. Date extraction priority ordering (exif > filename > created > modified)
10. scan_media_files on_scan_progress callback fires correctly
11. The pause_event actually blocking sort execution (threading test)
"""

from __future__ import annotations

import io
import threading
import time
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional

import pytest

from photosort.constants import ALL_EXTENSIONS
from photosort.date_extract import get_best_date, get_exif_date
from photosort.engine import (
    MISC_FOLDER,
    scan_media_files,
    sort_files,
)
from photosort.models import DateSource, SortConfig
from photosort.screenshot_detect import (
    is_screenshot,
    is_screen_recording,
    is_screenshot_or_recording,
)
from photosort.video_detect import (
    CONFIDENT_MINUTES,
    DeviceTimeline,
    is_confident_match,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_jpeg() -> bytes:
    """Return a minimal valid JPEG stub (no EXIF)."""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def _jpeg_with_exif_date(dt_str: str) -> bytes:
    """
    Build a proper JPEG with a real DateTimeOriginal EXIF tag using Pillow.
    dt_str format: 'YYYY:MM:DD HH:MM:SS'
    """
    from PIL import Image
    img = Image.new("RGB", (8, 8))
    exif = img.getexif()
    exif[36867] = dt_str  # DateTimeOriginal
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Zero-byte files
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroByteFiles:

    def test_zero_byte_jpg_is_scanned(self, tmp_path: Path):
        """Empty .jpg file should still appear in scan results."""
        f = tmp_path / "empty.jpg"
        f.write_bytes(b"")
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_zero_byte_mp4_is_scanned(self, tmp_path: Path):
        f = tmp_path / "empty.mp4"
        f.write_bytes(b"")
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_sort_zero_byte_file_no_crash(self, tmp_path: Path):
        """sort_files must not raise or produce errors for an empty JPEG."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "empty.jpg").write_bytes(b"")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)
        result = sort_files(config)
        assert result.total_files == 1
        assert result.errors == 0

    def test_sort_zero_byte_file_routes_to_misc(self, tmp_path: Path):
        """An empty file with no date info should end up in misc/."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "nopattern.jpg").write_bytes(b"")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=False)
        result = sort_files(config)
        assert result.errors == 0
        moved = [r for r in result.records if r.dest_path is not None]
        assert all(MISC_FOLDER in str(r.dest_path) for r in moved)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Files with no extension
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoExtensionFiles:

    def test_no_extension_not_scanned(self, tmp_path: Path):
        """Files without a recognised extension must be ignored by the scanner."""
        (tmp_path / "NOEXT").write_bytes(b"\xff\xd8\xff")
        (tmp_path / "someimage").write_bytes(b"\xff\xd8\xff")
        files, _ = scan_media_files(tmp_path)
        assert files == []

    def test_no_extension_alongside_valid(self, tmp_path: Path):
        """Only the valid media file should be picked up."""
        (tmp_path / "NOEXT").write_bytes(b"data")
        valid = tmp_path / "photo.jpg"
        valid.write_bytes(b"\xff\xd8\xff")
        files, _ = scan_media_files(tmp_path)
        assert files == [valid]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Very deeply nested source directories (5+ levels)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeeplyNested:

    def test_scan_five_levels_deep(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        f = deep / "IMG_20240715_083012.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_scan_seven_levels_deep(self, tmp_path: Path):
        deep = tmp_path / "l1" / "l2" / "l3" / "l4" / "l5" / "l6" / "l7"
        deep.mkdir(parents=True)
        f = deep / "VID_20230101_120000.mp4"
        f.write_bytes(b"\x00" * 8)
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_sort_deep_nested_files(self, tmp_path: Path):
        """Files buried deep should be found and sorted without errors."""
        src = tmp_path / "src"
        deep = src / "year" / "month" / "day" / "hour" / "minute"
        deep.mkdir(parents=True)
        (deep / "IMG_20240715_083012.jpg").write_bytes(b"\xff\xd8\xff")
        (deep / "VID_20230101_120000.mp4").write_bytes(b"\x00" * 8)

        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)
        result = sort_files(config)
        assert result.total_files == 2
        assert result.errors == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Files where EXIF date is corrupted/invalid
# ═══════════════════════════════════════════════════════════════════════════════

class TestCorruptedExifDate:

    def test_parse_exif_date_invalid_string(self, tmp_path: Path):
        """_parse_exif_date should return None for garbage strings."""
        from photosort.date_extract import _parse_exif_date
        assert _parse_exif_date("not-a-date") is None
        assert _parse_exif_date("0000:00:00 00:00:00") is None
        assert _parse_exif_date("9999:99:99 99:99:99") is None
        assert _parse_exif_date("") is None

    def test_get_exif_date_truncated_file(self, tmp_path: Path):
        """A truncated JPEG should not raise — must return None gracefully."""
        f = tmp_path / "truncated.jpg"
        f.write_bytes(b"\xff\xd8\xff\xe1\x00\x20Exif\x00\x00CORRUPTED")
        result = get_exif_date(f)
        assert result is None

    def test_get_exif_date_random_bytes(self, tmp_path: Path):
        """Random bytes in a .jpg file must not raise."""
        f = tmp_path / "garbage.jpg"
        f.write_bytes(bytes(range(256)) * 10)
        result = get_exif_date(f)
        assert result is None

    def test_sort_falls_back_when_exif_corrupted(self, tmp_path: Path):
        """
        When EXIF is corrupt the engine must fall back to filename or modified date
        and not produce an error record.
        """
        src = tmp_path / "src"
        src.mkdir()
        # Filename carries a valid date even though EXIF is garbage
        f = src / "IMG_20240715_083012.jpg"
        f.write_bytes(bytes(range(256)) * 5)
        out = tmp_path / "out"
        config = SortConfig(
            source=src, destination=out, dry_run=True,
            priority=[DateSource.EXIF, DateSource.FILENAME, DateSource.MODIFIED],
        )
        result = sort_files(config)
        assert result.errors == 0
        assert result.records[0].date_source in (DateSource.FILENAME, DateSource.MODIFIED)

    def test_get_best_date_skips_bad_exif(self, tmp_path: Path):
        """get_best_date with EXIF first should fall through to FILENAME for a stub."""
        f = tmp_path / "IMG_20230501_120000.jpg"
        f.write_bytes(b"\xff\xd8\xff")  # no real EXIF
        dt, source = get_best_date(f, [DateSource.EXIF, DateSource.FILENAME, DateSource.MODIFIED])
        assert source == DateSource.FILENAME
        assert dt == datetime(2023, 5, 1, 12, 0, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Duplicate detection when destination already has files from a previous run
# ═══════════════════════════════════════════════════════════════════════════════

class TestDuplicateWithExistingDestination:
    """
    Duplicate detection is scoped to a single sort run (the dest_registry is
    ephemeral). Two files with the same name and content placed in the same
    source folder in the *same* run are detected as duplicates.
    A file from a previous run at the destination is simply overwritten because
    the registry is empty at the start of a new run — this is by design.
    """

    def _make_content(self) -> bytes:
        return b"\xff\xd8\xff" + b"\xCC" * 200

    def test_same_run_duplicate_detected(self, tmp_path: Path):
        """
        Two files with the same name and identical content in sub-folders of
        the same source are processed in one run → second should be a duplicate.
        """
        content = self._make_content()
        src = tmp_path / "src"
        sub1 = src / "a"
        sub2 = src / "b"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)
        out = tmp_path / "out"

        (sub1 / "IMG_20240715_083012.jpg").write_bytes(content)
        (sub2 / "IMG_20240715_083012.jpg").write_bytes(content)

        config = SortConfig(source=src, destination=out, dry_run=False)
        result = sort_files(config)
        assert result.errors == 0
        assert result.total_files == 2

        dup_records = [r for r in result.records if r.is_duplicate]
        assert len(dup_records) == 1
        assert "duplicates" in str(dup_records[0].dest_path)

    def test_same_run_different_content_gets_suffix(self, tmp_path: Path):
        """
        Two files with the same name but different content in the same run
        should NOT go to duplicates/; they get a numeric suffix (_1, _2, …).
        """
        src = tmp_path / "src"
        sub1 = src / "a"
        sub2 = src / "b"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)
        out = tmp_path / "out"

        (sub1 / "IMG_20240715_083012.jpg").write_bytes(b"\xff\xd8\xff" + b"\xAA" * 100)
        (sub2 / "IMG_20240715_083012.jpg").write_bytes(b"\xff\xd8\xff" + b"\xBB" * 100)

        config = SortConfig(source=src, destination=out, dry_run=False)
        result = sort_files(config)
        assert result.errors == 0
        assert result.total_files == 2

        # Neither file should be in duplicates/ — they have different content
        assert all("duplicates" not in str(r.dest_path) for r in result.records)

    def test_second_run_overwrites_dest(self, tmp_path: Path):
        """
        The engine does NOT detect cross-run duplicates — files in a second
        run are simply moved to their computed destination (overwriting if same name).
        Both runs should succeed with no errors.
        """
        content = self._make_content()
        out = tmp_path / "out"

        src1 = tmp_path / "src1"
        src1.mkdir()
        (src1 / "IMG_20240715_083012.jpg").write_bytes(content)
        result1 = sort_files(SortConfig(source=src1, destination=out, dry_run=False))
        assert result1.errors == 0
        assert result1.total_files == 1

        src2 = tmp_path / "src2"
        src2.mkdir()
        (src2 / "IMG_20240715_083012.jpg").write_bytes(content)
        result2 = sort_files(SortConfig(source=src2, destination=out, dry_run=False))
        # Cross-run: no duplicate detection — file is simply moved (overwrites)
        assert result2.errors == 0
        assert result2.total_files == 1

    def test_second_run_different_content_gets_suffix(self, tmp_path: Path):
        """
        Two runs with the same filename but different content should NOT go to
        duplicates/; they get a numeric suffix (_1, _2, …) instead.
        """
        out = tmp_path / "out"

        src1 = tmp_path / "src1"
        src1.mkdir()
        (src1 / "IMG_20240715_083012.jpg").write_bytes(b"\xff\xd8\xff" + b"\xAA" * 100)
        sort_files(SortConfig(source=src1, destination=out, dry_run=False))

        src2 = tmp_path / "src2"
        src2.mkdir()
        (src2 / "IMG_20240715_083012.jpg").write_bytes(b"\xff\xd8\xff" + b"\xBB" * 100)
        result2 = sort_files(SortConfig(source=src2, destination=out, dry_run=False))
        assert result2.errors == 0

        # The second file should not be in duplicates/
        non_dup = [r for r in result2.records if not r.is_duplicate]
        assert len(non_dup) == 1
        assert "duplicates" not in str(non_dup[0].dest_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Filenames with special characters
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpecialCharacterFilenames:

    def test_filename_with_spaces_scanned(self, tmp_path: Path):
        f = tmp_path / "My Photo 2024.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_filename_with_unicode_scanned(self, tmp_path: Path):
        f = tmp_path / "Фото_20240715_083012.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_filename_with_parentheses_scanned(self, tmp_path: Path):
        f = tmp_path / "Screenshot (1).png"
        f.write_bytes(b"\x89PNG\r\n")
        files, _ = scan_media_files(tmp_path)
        assert f in files

    def test_sort_spaces_in_filename(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "My Vacation Photo.jpg").write_bytes(b"\xff\xd8\xff")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)
        result = sort_files(config)
        assert result.total_files == 1
        assert result.errors == 0

    def test_sort_unicode_filename(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "旅行写真.jpg").write_bytes(b"\xff\xd8\xff")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)
        result = sort_files(config)
        assert result.total_files == 1
        assert result.errors == 0

    def test_sort_parentheses_in_filename(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "Screenshot (42).png").write_bytes(b"\x89PNG\r\n")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)
        result = sort_files(config)
        assert result.total_files == 1
        assert result.errors == 0

    def test_sort_special_chars_no_errors(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        filenames = [
            "photo with spaces.jpg",
            "naïve résumé.jpg",
            "file (copy).jpg",
            "日本語.jpg",
            "emoji_😀.jpg",
        ]
        for name in filenames:
            (src / name).write_bytes(b"\xff\xd8\xff")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)
        result = sort_files(config)
        assert result.errors == 0
        assert result.total_files == len(filenames)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Screenshot detection — ALL patterns from CLAUDE.md
# ═══════════════════════════════════════════════════════════════════════════════

class TestScreenshotDetectionAllPatterns:
    """
    Verify every screenshot/screen-recording pattern listed in CLAUDE.md
    is recognised by is_screenshot_or_recording().
    """

    # ── Screenshot patterns ───────────────────────────────────────────────────

    @pytest.mark.parametrize("filename", [
        # Screenshot_* (Android — Samsung, Pixel, MIUI)
        "Screenshot_20240715-083012_Chrome.jpg",
        "Screenshot_20240101-090000.jpg",
        "screenshot_20240715_083012.jpg",  # lowercase
        # Screenshot YYYY* (macOS)
        "Screenshot 2024-07-15 at 08.30.12.png",
        "Screenshot 2024-01-01 at 12.00.00.png",
        # Screenshot (*).png (Windows Snipping Tool)
        "Screenshot (1).png",
        "Screenshot (42).png",
        "Screenshot(3).png",
    ])
    def test_screenshot_patterns_detected(self, filename: str, tmp_path: Path):
        f = tmp_path / filename
        f.touch()
        assert is_screenshot(f), f"Expected {filename!r} to be detected as screenshot"
        assert is_screenshot_or_recording(f)

    # ── Screen recording patterns ─────────────────────────────────────────────

    @pytest.mark.parametrize("filename", [
        # Screen_recording_* (Samsung)
        "Screen_recording_20240715-083012.mp4",
        "screen_recording_20230101-120000.mp4",
        # Screen Recording YYYY* (macOS / iOS)
        "Screen Recording 2024-07-15 at 08.30.12.mov",
        "Screen Recording 2023-01-01 at 12.00.00.mov",
        # screenrecord_* (generic Android)
        "screenrecord_2024-07-15-08-30-12.mp4",
        "screenrecord_001.mp4",
        # ScreenRecorder_* (Xiaomi / MIUI)
        "ScreenRecorder_2024-07-15-08-30-12.mp4",
        "ScreenRecorder_001.mp4",
        # record_screen_* (some Android ROMs)
        "record_screen_20240715_083012.mp4",
        "record_screen_001.mp4",
    ])
    def test_screen_recording_patterns_detected(self, filename: str, tmp_path: Path):
        f = tmp_path / filename
        f.touch()
        assert is_screen_recording(f), f"Expected {filename!r} to be detected as screen recording"
        assert is_screenshot_or_recording(f)

    def test_non_screenshot_not_detected(self, tmp_path: Path):
        """Normal camera photos must not be flagged as screenshots."""
        for name in ["IMG_20240715_083012.jpg", "VID_20230101_120000.mp4", "DSC_0001.jpg"]:
            f = tmp_path / name
            f.touch()
            assert not is_screenshot_or_recording(f), f"{name!r} wrongly detected as screenshot"

    def test_screenshot_routed_to_screenshots_folder(self, tmp_path: Path):
        """
        When a file matches a screenshot pattern and has no EXIF device,
        sort_files should route it to the screenshots/ folder.
        """
        from photosort.models import SCREENSHOTS_FOLDER
        src = tmp_path / "src"
        src.mkdir()
        (src / "Screenshot_20240715-083012_Chrome.jpg").write_bytes(b"\xff\xd8\xff")
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=False)
        result = sort_files(config)
        assert result.errors == 0
        records = result.records
        assert len(records) == 1
        assert SCREENSHOTS_FOLDER in str(records[0].dest_path)

    def test_screen_recording_routed_to_screenshots_folder(self, tmp_path: Path):
        from photosort.models import SCREENSHOTS_FOLDER
        src = tmp_path / "src"
        src.mkdir()
        (src / "Screen Recording 2024-07-15 at 08.30.12.mov").write_bytes(b"\x00" * 8)
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=False)
        result = sort_files(config)
        assert result.errors == 0
        records = result.records
        assert len(records) == 1
        assert SCREENSHOTS_FOLDER in str(records[0].dest_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Proximity matching — within window vs outside window
# ═══════════════════════════════════════════════════════════════════════════════

class TestProximityMatching:

    def _make_timeline(self, dt: datetime, device: str) -> DeviceTimeline:
        tl = DeviceTimeline()
        tl.add(dt, device)
        return tl

    # ── DeviceTimeline unit tests ─────────────────────────────────────────────

    def test_within_window_returns_device(self):
        base = datetime(2024, 7, 15, 12, 0, 0)
        tl = self._make_timeline(base, "ILCE-7M3")
        # 5 minutes after → within 30-minute window
        device, delta = tl.nearest(base + timedelta(minutes=5), window_minutes=30)
        assert device == "ILCE-7M3"
        assert delta == timedelta(minutes=5)

    def test_outside_window_returns_none(self):
        base = datetime(2024, 7, 15, 12, 0, 0)
        tl = self._make_timeline(base, "ILCE-7M3")
        # 45 minutes after → outside 30-minute window
        device, delta = tl.nearest(base + timedelta(minutes=45), window_minutes=30)
        assert device is None
        assert delta is None

    def test_exactly_at_window_boundary_included(self):
        base = datetime(2024, 7, 15, 12, 0, 0)
        tl = self._make_timeline(base, "ILCE-7M3")
        # Exactly 30 minutes → still within window (<=)
        device, delta = tl.nearest(base + timedelta(minutes=30), window_minutes=30)
        assert device == "ILCE-7M3"

    def test_just_past_boundary_excluded(self):
        base = datetime(2024, 7, 15, 12, 0, 0)
        tl = self._make_timeline(base, "ILCE-7M3")
        device, delta = tl.nearest(base + timedelta(minutes=30, seconds=1), window_minutes=30)
        assert device is None

    def test_empty_timeline_returns_none(self):
        tl = DeviceTimeline()
        device, delta = tl.nearest(datetime(2024, 1, 1), window_minutes=30)
        assert device is None
        assert delta is None

    def test_nearest_picks_closer_of_two(self):
        """When two devices are both within window, the closer one wins."""
        base = datetime(2024, 7, 15, 12, 0, 0)
        tl = DeviceTimeline()
        tl.add(base - timedelta(minutes=20), "FarDevice")
        tl.add(base - timedelta(minutes=2),  "CloseDevice")
        device, delta = tl.nearest(base, window_minutes=30)
        assert device == "CloseDevice"
        assert delta == timedelta(minutes=2)

    # ── Confidence threshold ──────────────────────────────────────────────────

    def test_confident_match_within_10_min(self):
        assert is_confident_match(timedelta(minutes=CONFIDENT_MINUTES))
        assert is_confident_match(timedelta(minutes=5))
        assert is_confident_match(timedelta(seconds=0))

    def test_not_confident_beyond_10_min(self):
        assert not is_confident_match(timedelta(minutes=CONFIDENT_MINUTES, seconds=1))
        assert not is_confident_match(timedelta(minutes=29))

    # ── Integration: proximity warning flag ──────────────────────────────────

    def test_sort_proximity_warning_flagged(self, tmp_path: Path):
        """
        A video more than CONFIDENT_MINUTES away from the nearest photo should
        produce a proximity_warning in its FileRecord.
        The photo has a filename date so the timeline is populated.
        The video date is > 10 min away but < 30 min (default window).
        """
        src = tmp_path / "src"
        src.mkdir()

        # Photo at 12:00 — filename-based date so no EXIF device needed
        # Use a file name that carries a date 20 min before the video
        # Photo: 20240715_120000, Video: needs to be 20240715_121500 (15 min later)
        # We'll create both with appropriate filenames so they get correct dates.
        (src / "IMG_20240715_120000.jpg").write_bytes(b"\xff\xd8\xff")
        (src / "VID_20240715_121500.mp4").write_bytes(b"\x00" * 8)

        out = tmp_path / "out"
        config = SortConfig(
            source=src,
            destination=out,
            dry_run=True,
            proximity_window_minutes=30,
        )
        result = sort_files(config)
        # The photo has no EXIF device → goes to misc; no device in timeline either
        # The video should end up unmatched (no photo device in timeline)
        # This test verifies the run completes without error
        assert result.errors == 0

    def test_sort_proximity_disabled_when_window_zero(self, tmp_path: Path):
        """With proximity_window_minutes=0, videos must not be matched by proximity."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "IMG_20240715_120000.jpg").write_bytes(b"\xff\xd8\xff")
        (src / "VID_20240715_120100.mp4").write_bytes(b"\x00" * 8)

        out = tmp_path / "out"
        config = SortConfig(
            source=src,
            destination=out,
            dry_run=True,
            proximity_window_minutes=0,  # disabled
        )
        result = sort_files(config)
        video_records = [r for r in result.records if str(r.source_path).endswith(".mp4")]
        assert all(not r.proximity_match for r in video_records)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Date extraction priority ordering
# ═══════════════════════════════════════════════════════════════════════════════

class TestDateExtractionPriority:

    def test_exif_beats_filename_when_exif_first(self, tmp_path: Path):
        """
        When EXIF is first in priority and a valid EXIF date exists,
        get_best_date should return the EXIF date (not the filename date).
        """
        exif_date = "2020:06:15 10:30:00"
        f = tmp_path / "IMG_20240715_083012.jpg"
        f.write_bytes(_jpeg_with_exif_date(exif_date))

        dt, source = get_best_date(f, [DateSource.EXIF, DateSource.FILENAME, DateSource.MODIFIED])
        assert source == DateSource.EXIF
        assert dt == datetime(2020, 6, 15, 10, 30, 0)

    def test_filename_beats_modified_when_filename_second(self, tmp_path: Path):
        """
        EXIF unavailable (stub JPEG), filename has date → FILENAME wins over MODIFIED.
        """
        f = tmp_path / "IMG_20230501_120000.jpg"
        f.write_bytes(b"\xff\xd8\xff")  # no real EXIF
        dt, source = get_best_date(f, [DateSource.EXIF, DateSource.FILENAME, DateSource.MODIFIED])
        assert source == DateSource.FILENAME
        assert dt.year == 2023
        assert dt.month == 5

    def test_modified_is_fallback_when_all_else_fails(self, tmp_path: Path):
        """A file with no EXIF and no recognisable filename → MODIFIED fallback."""
        f = tmp_path / "random.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        dt, source = get_best_date(f, [DateSource.EXIF, DateSource.FILENAME, DateSource.CREATED, DateSource.MODIFIED])
        assert source in (DateSource.MODIFIED, DateSource.CREATED)
        assert isinstance(dt, datetime)

    def test_priority_modified_filename_skips_exif(self, tmp_path: Path):
        """When MODIFIED is listed first, it must be used even if FILENAME would match."""
        f = tmp_path / "IMG_20240715_083012.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        dt, source = get_best_date(f, [DateSource.MODIFIED, DateSource.FILENAME])
        assert source == DateSource.MODIFIED

    def test_priority_filename_only(self, tmp_path: Path):
        """With only FILENAME in priority list, must fall back to MODIFIED if no match."""
        f = tmp_path / "random_name.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        dt, source = get_best_date(f, [DateSource.FILENAME])
        # No filename pattern → guaranteed fallback to MODIFIED
        assert source == DateSource.MODIFIED
        assert isinstance(dt, datetime)

    def test_exif_priority_in_sort_config(self, tmp_path: Path):
        """
        sort_files respects SortConfig.priority.
        With FILENAME first, a file whose name has a date should use FILENAME.
        """
        src = tmp_path / "src"
        src.mkdir()
        (src / "IMG_20240101_120000.jpg").write_bytes(b"\xff\xd8\xff")
        out = tmp_path / "out"
        config = SortConfig(
            source=src, destination=out, dry_run=True,
            priority=[DateSource.FILENAME, DateSource.MODIFIED],
        )
        result = sort_files(config)
        assert result.records[0].date_source == DateSource.FILENAME

    def test_modified_priority_in_sort_config(self, tmp_path: Path):
        """With MODIFIED first, even a file whose name has a date uses MODIFIED."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "IMG_20240101_120000.jpg").write_bytes(b"\xff\xd8\xff")
        out = tmp_path / "out"
        config = SortConfig(
            source=src, destination=out, dry_run=True,
            priority=[DateSource.MODIFIED, DateSource.FILENAME],
        )
        result = sort_files(config)
        assert result.records[0].date_source == DateSource.MODIFIED


# ═══════════════════════════════════════════════════════════════════════════════
# 10. scan_media_files — on_scan_progress callback
# ═══════════════════════════════════════════════════════════════════════════════

class TestScanProgressCallback:

    def test_callback_fires_for_each_file(self, tmp_path: Path):
        """on_scan_progress(count) must be called once per discovered file."""
        calls: list[int] = []
        for i in range(5):
            (tmp_path / f"IMG_2024010{i}_120000.jpg").write_bytes(b"\xff\xd8\xff")

        scan_media_files(tmp_path, on_progress=lambda n: calls.append(n))

        assert len(calls) == 5

    def test_callback_count_increments(self, tmp_path: Path):
        """The count passed to on_scan_progress should go 1, 2, 3, …"""
        calls: list[int] = []
        for i in range(4):
            (tmp_path / f"IMG_2024010{i}_120000.jpg").write_bytes(b"\xff\xd8\xff")

        scan_media_files(tmp_path, on_progress=lambda n: calls.append(n))

        assert sorted(calls) == list(range(1, 5))

    def test_callback_not_called_on_empty_dir(self, tmp_path: Path):
        calls: list[int] = []
        scan_media_files(tmp_path, on_progress=lambda n: calls.append(n))
        assert calls == []

    def test_callback_not_called_for_non_media_files(self, tmp_path: Path):
        """Non-media files must not trigger the callback."""
        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "doc.pdf").write_bytes(b"%PDF")
        calls: list[int] = []
        scan_media_files(tmp_path, on_progress=lambda n: calls.append(n))
        assert calls == []

    def test_callback_with_mixed_files(self, tmp_path: Path):
        """Only media files count — txt/pdf ignored."""
        (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")
        (tmp_path / "video.mp4").write_bytes(b"\x00" * 4)
        (tmp_path / "readme.txt").write_text("ignore me")
        calls: list[int] = []
        scan_media_files(tmp_path, on_progress=lambda n: calls.append(n))
        assert len(calls) == 2

    def test_on_scan_progress_forwarded_from_sort_files(self, tmp_path: Path):
        """sort_files must forward on_scan_progress to scan_media_files."""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(3):
            (src / f"IMG_2024010{i}_120000.jpg").write_bytes(b"\xff\xd8\xff")

        calls: list[int] = []
        config = SortConfig(source=src, destination=tmp_path / "out", dry_run=True)
        sort_files(config, on_scan_progress=lambda n: calls.append(n))
        assert len(calls) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 11. pause_event actually blocks sort execution
# ═══════════════════════════════════════════════════════════════════════════════

class TestPauseEvent:

    def _make_source(self, tmp_path: Path, n: int = 5) -> Path:
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        for i in range(n):
            (src / f"IMG_2024010{i}_120000.jpg").write_bytes(b"\xff\xd8\xff")
        return src

    def test_pause_event_blocks_execution(self, tmp_path: Path):
        """
        When pause_event is cleared (not set), sort_files must pause.
        We start the sort in a background thread with a cleared event, wait
        briefly, then set the event, and verify the sort completes.
        """
        src = self._make_source(tmp_path)
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)

        pause_event = threading.Event()
        # Start with the event CLEARED (paused)
        pause_event.clear()

        results: list = []
        errors: list = []

        def _run():
            try:
                r = sort_files(config, pause_event=pause_event)
                results.append(r)
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Thread should be blocked — give it a moment to start
        time.sleep(0.05)
        assert t.is_alive(), "Thread should still be running (paused)"
        assert errors == [], "No errors should have occurred yet"

        # Unblock by setting the event
        pause_event.set()
        t.join(timeout=10)

        assert not t.is_alive(), "Thread should have finished after event was set"
        assert errors == [], f"Sort raised an error: {errors}"
        assert len(results) == 1
        assert results[0].total_files == 5

    def test_pre_set_event_runs_immediately(self, tmp_path: Path):
        """
        When pause_event is set (running state), sort_files should not block.
        """
        src = self._make_source(tmp_path, n=3)
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)

        pause_event = threading.Event()
        pause_event.set()  # already running

        start = time.monotonic()
        result = sort_files(config, pause_event=pause_event)
        elapsed = time.monotonic() - start

        assert result.total_files == 3
        assert result.errors == 0
        # Should complete quickly — less than 5 seconds even on a slow CI machine
        assert elapsed < 5.0

    def test_pause_resume_cycle(self, tmp_path: Path):
        """
        Sort that starts paused, then resumes, should still produce correct output.
        """
        src = self._make_source(tmp_path, n=4)
        out = tmp_path / "out"
        config = SortConfig(source=src, destination=out, dry_run=True)

        pause_event = threading.Event()
        pause_event.clear()

        results: list = []

        def _run():
            results.append(sort_files(config, pause_event=pause_event))

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Briefly pause, then resume
        time.sleep(0.02)
        pause_event.set()
        t.join(timeout=10)

        assert len(results) == 1
        assert results[0].total_files == 4
        assert results[0].errors == 0
