"""Data models shared across the entire application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional


class DateSource(Enum):
    EXIF = "exif"
    FILENAME = "filename"
    CREATED = "created"    # st_birthtime (macOS/Windows) — skipped on Linux
    MODIFIED = "modified"

    def label(self) -> str:
        return self.value.capitalize()


DEFAULT_PRIORITY: list[DateSource] = [
    DateSource.EXIF,
    DateSource.FILENAME,
    DateSource.CREATED,
    DateSource.MODIFIED,
]

DEFAULT_DATE_FORMAT = "%Y/%Y-%m/%Y-%m-%d"

DATE_FORMAT_PRESETS: list[tuple[str, str]] = [
    ("%Y/%Y-%m/%Y-%m-%d", "Year / Year-Month / Year-Month-Day  (default)"),
    ("%Y/%Y-%m",          "Year / Year-Month"),
    ("%Y/%Y-%m-%d",       "Year / Year-Month-Day"),
    ("%Y",                "Year only"),
]


UNMATCHED_VIDEO_FOLDER = "unmatched-videos"
SCREENSHOTS_FOLDER     = "screenshots"
DEFAULT_PROXIMITY_WINDOW = 30  # minutes


@dataclass
class SortConfig:
    source: Path
    destination: Path
    dry_run: bool = False
    date_format: str = DEFAULT_DATE_FORMAT
    priority: list[DateSource] = field(default_factory=lambda: list(DEFAULT_PRIORITY))
    proximity_window_minutes: int = DEFAULT_PROXIMITY_WINDOW  # 0 = disabled
    workers: int = 1  # parallel threads; 1 = single-threaded (safe for HDDs)


@dataclass
class FileRecord:
    """Processing result for a single file."""
    source_path: Path
    dest_path: Optional[Path]            # None on error
    date: Optional[datetime]
    date_source: Optional[DateSource]
    device: Optional[str]                # Sanitized device name, or None → misc/unmatched
    sha256: Optional[str]                # Only populated when a collision was checked
    is_duplicate: bool
    duplicate_index: int                 # 0 = original; 1 = D1, 2 = D2, …
    error: Optional[str]
    # Video proximity matching
    proximity_match: bool = False        # True if device was inferred by proximity
    proximity_delta: Optional[timedelta] = None  # time gap to nearest photo
    proximity_warning: bool = False      # True if delta > CONFIDENT_MINUTES


@dataclass
class SortResult:
    """Aggregate outcome of a sort run."""
    total_files: int = 0
    moved: int = 0
    duplicates: int = 0
    errors: int = 0
    skipped: int = 0                       # files skipped via .photosort-skip
    proximity_warnings: int = 0            # videos matched by proximity with low confidence
    by_source: dict[str, int] = field(default_factory=lambda: {
        s.value: 0 for s in DateSource
    })
    by_device: dict[str, int] = field(default_factory=dict)
    by_extension: dict[str, int] = field(default_factory=dict)
    undo_log: list[tuple[str, str]] = field(default_factory=list)
    records: list[FileRecord] = field(default_factory=list)
