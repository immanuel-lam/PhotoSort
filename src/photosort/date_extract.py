"""Date extraction from EXIF metadata, filenames, and filesystem timestamps."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from photosort.constants import EXIF_DATE_TAGS, FILENAME_PATTERNS, PHOTO_EXTENSIONS
from photosort.models import DateSource

# ── Optional dependency ───────────────────────────────────────────────────────
try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False


# ── EXIF ──────────────────────────────────────────────────────────────────────

def _parse_exif_date(dt_str: str) -> Optional[datetime]:
    """Parse an EXIF datetime string ('YYYY:MM:DD HH:MM:SS')."""
    try:
        return datetime.strptime(dt_str.strip(), '%Y:%m:%d %H:%M:%S')
    except (ValueError, AttributeError):
        return None


def get_exif_date(filepath: Path) -> Optional[datetime]:
    """Return the EXIF capture date of a photo, or None if unavailable."""
    if not PILLOW_AVAILABLE:
        return None
    if filepath.suffix.lower() not in PHOTO_EXTENSIONS:
        return None
    try:
        with Image.open(filepath) as img:
            exif_raw = img.getexif()
            if not exif_raw:
                return None
            for tag_id in EXIF_DATE_TAGS:
                value = exif_raw.get(tag_id)
                if value:
                    dt = _parse_exif_date(value)
                    if dt:
                        return dt
    except Exception:
        pass
    return None


# ── Filename ──────────────────────────────────────────────────────────────────

def get_filename_date(filepath: Path) -> Optional[datetime]:
    """Try to extract a date from camera/phone-style filenames."""
    stem = filepath.stem
    for pattern in FILENAME_PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        groups = match.groups()
        try:
            if len(groups) == 2:
                raw_date, raw_time = groups
                date_digits = re.sub(r'\D', '', raw_date)
                time_digits = re.sub(r'\D', '', raw_time)
                if len(date_digits) == 8 and len(time_digits) >= 6:
                    return datetime.strptime(
                        date_digits + time_digits[:6], '%Y%m%d%H%M%S'
                    )
            elif len(groups) == 1:
                date_digits = re.sub(r'\D', '', groups[0])
                if len(date_digits) == 8:
                    return datetime.strptime(date_digits, '%Y%m%d')
        except ValueError:
            continue
    return None


# ── Filesystem timestamps ─────────────────────────────────────────────────────

def get_created_date(filepath: Path) -> Optional[datetime]:
    """
    Return the file creation time, or None if unavailable.
    - macOS: stat().st_birthtime
    - Windows: stat().st_ctime (creation time on Windows)
    - Linux: unavailable (st_ctime is inode change time) → returns None
    """
    try:
        stat = filepath.stat()
        if sys.platform == 'win32':
            return datetime.fromtimestamp(stat.st_ctime)
        # macOS and some BSDs expose st_birthtime
        birthtime = getattr(stat, 'st_birthtime', None)
        if birthtime is not None:
            return datetime.fromtimestamp(birthtime)
    except Exception:
        pass
    return None


def get_modified_date(filepath: Path) -> datetime:
    """Return the file's last-modified time as a datetime (always succeeds)."""
    return datetime.fromtimestamp(filepath.stat().st_mtime)


# ── Priority chain ────────────────────────────────────────────────────────────

_EXTRACTORS: dict[DateSource, Callable[[Path], Optional[datetime]]] = {
    DateSource.EXIF:     get_exif_date,
    DateSource.FILENAME: get_filename_date,
    DateSource.CREATED:  get_created_date,
    DateSource.MODIFIED: get_modified_date,  # type: ignore[dict-item]
}


def get_best_date(
    filepath: Path,
    priority: list[DateSource],
) -> tuple[datetime, DateSource]:
    """
    Return (datetime, source) using the highest-priority date available.
    Tries each source in *priority* order; falls back to MODIFIED if all fail.
    """
    for source in priority:
        extractor = _EXTRACTORS.get(source)
        if extractor is None:
            continue
        dt = extractor(filepath)
        if dt is not None:
            return dt, source
    # Guaranteed fallback
    return get_modified_date(filepath), DateSource.MODIFIED
