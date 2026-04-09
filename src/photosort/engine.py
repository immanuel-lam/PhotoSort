"""Core sorting engine — shared by CLI and GUI."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from photosort.constants import ALL_EXTENSIONS, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from photosort.date_extract import get_best_date
from photosort.device_detect import get_device_name
from photosort.hashing import sha256_file
from photosort.models import (
    SCREENSHOTS_FOLDER,
    UNMATCHED_VIDEO_FOLDER,
    DateSource,
    FileRecord,
    SortConfig,
    SortResult,
)
from photosort.screenshot_detect import is_screenshot_or_recording
from photosort.video_detect import (
    CONFIDENT_MINUTES,
    DeviceTimeline,
    get_video_device_from_metadata,
    is_confident_match,
)

# Folder name used when a photo has no EXIF device info
MISC_FOLDER = "misc"

ProgressCallback = Callable[[int, int, FileRecord], None]


# ── Scanning ──────────────────────────────────────────────────────────────────

def scan_media_files(source: Path) -> list[Path]:
    """Return all media files under *source* (recursive), sorted by path."""
    return sorted(
        f for f in source.rglob('*')
        if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS
    )


def _split_photos_videos(files: list[Path]) -> tuple[list[Path], list[Path]]:
    """Split a file list into (photos, videos)."""
    photos = [f for f in files if f.suffix.lower() in PHOTO_EXTENSIONS]
    videos = [f for f in files if f.suffix.lower() in VIDEO_EXTENSIONS]
    return photos, videos


# ── Destination computation ───────────────────────────────────────────────────

def _format_date_path(dt, date_format: str) -> Path:
    """
    Convert a datetime and format string into a relative folder path.
    The format string uses strftime codes; '/' separates path segments.
    Example: "%Y/%Y-%m/%Y-%m-%d" → Path("2024/2024-07/2024-07-15")
    """
    segments = [dt.strftime(seg) for seg in date_format.split('/')]
    return Path(*segments)


def compute_destination(
    filepath: Path,
    dt,
    device: Optional[str],
    date_format: str,
    base_dest: Path,
) -> Path:
    """
    Build the intended destination path (before duplicate resolution):
        <base_dest>/<device>/<date_path>/<filename>
    Uses MISC_FOLDER when device is None.
    """
    folder = device if device else MISC_FOLDER
    date_path = _format_date_path(dt, date_format)
    return base_dest / folder / date_path / filepath.name


# ── Duplicate resolution ──────────────────────────────────────────────────────

def resolve_duplicate(
    intended_dest: Path,
    source_path: Path,
    dest_registry: dict[Path, str],
    base_dest: Path,
    dry_run: bool,
) -> tuple[Path, bool, int]:
    """
    Determine the final destination path, handling duplicates via lazy hashing.

    - If *intended_dest* is not in *dest_registry*: register and return as-is.
    - If there is a collision:
        - Hash the incoming file and the previously placed file.
        - Same hash (true duplicate): route to duplicates/ subtree, Dn subfolder.
        - Different hash (name collision only): append _1, _2… to stem.

    Returns (final_path, is_duplicate, duplicate_index).
      duplicate_index 0 = original; 1 = D1, 2 = D2, …
    Updates *dest_registry* in place.
    """
    if intended_dest not in dest_registry:
        dest_registry[intended_dest] = ""  # placeholder; hash filled lazily
        return intended_dest, False, 0

    # --- Collision: hash both files to decide what to do ---
    incoming_hash = sha256_file(source_path)

    existing_hash = dest_registry[intended_dest]
    if not existing_hash:
        # First time this slot was actually filled — compute the existing file's hash
        if not dry_run and intended_dest.exists():
            existing_hash = sha256_file(intended_dest)
        else:
            existing_hash = ""
        dest_registry[intended_dest] = existing_hash

    if incoming_hash and existing_hash and incoming_hash == existing_hash:
        # True duplicate — route to duplicates/ Dn/
        dup_n = _next_duplicate_index(intended_dest, dest_registry)
        dup_dest = _duplicate_path(intended_dest, base_dest, dup_n)
        dest_registry[dup_dest] = incoming_hash
        return dup_dest, True, dup_n

    # Different files, same name — keep in main folder with numeric suffix
    final = _safe_suffix(intended_dest)
    dest_registry[final] = incoming_hash
    return final, False, 0


def _duplicate_path(intended_dest: Path, base_dest: Path, dup_n: int) -> Path:
    """
    Mirror the intended destination into the duplicates/ subtree with a Dn subfolder.
    intended_dest:  <base_dest>/<device>/<date_path>/<filename>
    result:         <base_dest>/<device>/duplicates/<date_path>/D{n}/<filename>
    """
    rel = intended_dest.relative_to(base_dest)
    parts = rel.parts  # (device, *date_parts, filename)
    device_part   = parts[0]
    date_parts    = parts[1:-1]
    filename_part = parts[-1]
    return base_dest / device_part / "duplicates" / Path(*date_parts) / f"D{dup_n}" / filename_part


def _next_duplicate_index(intended_dest: Path, dest_registry: dict[Path, str]) -> int:
    """Count how many D-n entries already exist for this destination slot."""
    base = str(intended_dest)
    existing = [k for k in dest_registry if "duplicates" in str(k) and k.name == intended_dest.name]
    return len(existing) + 1


def _safe_suffix(path: Path) -> Path:
    """Append _1, _2, … to stem until a path not in the registry is found."""
    counter = 1
    while True:
        candidate = path.with_stem(f"{path.stem}_{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


# ── File move (cross-platform) ────────────────────────────────────────────────

def _move_file(src: Path, dst: Path) -> None:
    """
    Move *src* to *dst*, handling cross-drive moves on Windows.
    shutil.move uses os.rename first; on Windows that raises OSError across
    drives, so we fall back to copy-then-delete.
    """
    try:
        shutil.move(str(src), str(dst))
    except OSError:
        shutil.copy2(str(src), str(dst))
        src.unlink()


# ── Main orchestrator ─────────────────────────────────────────────────────────

def sort_files(
    config: SortConfig,
    on_progress: Optional[ProgressCallback] = None,
) -> SortResult:
    """
    Two-pass sort:
      Pass 1 — photos only: detect EXIF device, build DeviceTimeline.
      Pass 2 — videos only: try container metadata, then proximity match.

    Progress is reported via on_progress(current_index, total, FileRecord).
    """
    result = SortResult()
    dest_registry: dict[Path, str] = {}
    timeline = DeviceTimeline()

    all_files = scan_media_files(config.source)
    photos, videos = _split_photos_videos(all_files)
    ordered = photos + videos          # photos always processed first
    result.total_files = len(ordered)

    for idx, filepath in enumerate(ordered, start=1):
        is_video = filepath.suffix.lower() in VIDEO_EXTENSIONS
        record = _process_file(filepath, config, dest_registry, timeline, is_video)
        result.records.append(record)

        if record.error:
            result.errors += 1
        elif record.is_duplicate:
            result.duplicates += 1
            result.moved += 1
        else:
            result.moved += 1

        if record.proximity_warning:
            result.proximity_warnings += 1

        if record.date_source:
            result.by_source[record.date_source.value] = (
                result.by_source.get(record.date_source.value, 0) + 1
            )

        device_key = record.device or (UNMATCHED_VIDEO_FOLDER if is_video else MISC_FOLDER)
        result.by_device[device_key] = result.by_device.get(device_key, 0) + 1

        if on_progress:
            on_progress(idx, result.total_files, record)

    return result


def _process_file(
    filepath: Path,
    config: SortConfig,
    dest_registry: dict[Path, str],
    timeline: DeviceTimeline,
    is_video: bool,
) -> FileRecord:
    prox_match   = False
    prox_delta   = None
    prox_warning = False

    try:
        dt, date_source = get_best_date(filepath, config.priority)

        if is_video:
            # 1. Try container metadata (MP4/MOV atoms)
            device = get_video_device_from_metadata(filepath)
            # 2. Proximity match against photo timeline
            if not device and config.proximity_window_minutes > 0 and dt:
                matched_device, delta = timeline.nearest(dt, config.proximity_window_minutes)
                if matched_device:
                    device       = matched_device
                    prox_match   = True
                    prox_delta   = delta
                    prox_warning = not is_confident_match(delta)
            if device:
                folder = device
            elif is_screenshot_or_recording(filepath):
                folder = SCREENSHOTS_FOLDER
            else:
                folder = UNMATCHED_VIDEO_FOLDER
        else:
            # Photo: EXIF device, add to timeline for later video matching
            device = get_device_name(filepath)
            if device and dt:
                timeline.add(dt, device)
            if device:
                folder = device
            elif is_screenshot_or_recording(filepath):
                folder = SCREENSHOTS_FOLDER
            else:
                folder = MISC_FOLDER

        intended = (
            config.destination / folder
            / _format_date_path(dt, config.date_format)
            / filepath.name
        )

        final_path, is_dup, dup_idx = resolve_duplicate(
            intended, filepath, dest_registry, config.destination, config.dry_run
        )

        sha = dest_registry.get(final_path) or dest_registry.get(intended) or ""

        if not config.dry_run:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            _move_file(filepath, final_path)

        return FileRecord(
            source_path=filepath,
            dest_path=final_path,
            date=dt,
            date_source=date_source,
            device=device,
            sha256=sha or None,
            is_duplicate=is_dup,
            duplicate_index=dup_idx,
            error=None,
            proximity_match=prox_match,
            proximity_delta=prox_delta,
            proximity_warning=prox_warning,
        )

    except Exception as exc:
        return FileRecord(
            source_path=filepath,
            dest_path=None,
            date=None,
            date_source=None,
            device=None,
            sha256=None,
            is_duplicate=False,
            duplicate_index=0,
            error=str(exc),
        )
