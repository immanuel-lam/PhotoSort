"""Core sorting engine — shared by CLI and GUI."""

from __future__ import annotations

import csv
import fnmatch
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
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

# Log file written to the destination after every live run
LOG_FILENAME        = "photosort-log.csv"
UNDO_SH_FILENAME    = "photosort-undo.sh"
UNDO_BAT_FILENAME   = "photosort-undo.bat"
CHECKPOINT_FILENAME = "photosort-checkpoint.json"

ProgressCallback     = Callable[[int, int, FileRecord], None]
ScanProgressCallback = Callable[[int], None]
ConfirmCallback      = Callable[[int, int, int], bool]  # (media_count, non_media_count, total_bytes) -> proceed


# ── Skip list ─────────────────────────────────────────────────────────────────

def _load_skip_patterns(source: Path) -> list[str]:
    """
    Read glob patterns from <source>/.photosort-skip.
    Lines starting with '#' and blank lines are ignored.
    Returns an empty list if the file does not exist.
    """
    skip_file = source / ".photosort-skip"
    if not skip_file.exists():
        return []
    patterns: list[str] = []
    for line in skip_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _matches_skip(filepath: Path, source: Path, patterns: list[str]) -> bool:
    """Return True if *filepath* matches any skip pattern (name or relative path)."""
    name = filepath.name
    try:
        rel = str(filepath.relative_to(source))
    except ValueError:
        rel = name
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
    return False


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _write_checkpoint(
    path: Path,
    processed: set[str],
    timeline: DeviceTimeline,
    dest_registry: dict[Path, str],
    result: SortResult,
    config: SortConfig,
) -> None:
    """
    Atomically write a checkpoint file so an interrupted run can be resumed.
    Uses a .tmp write-then-rename to avoid leaving a corrupt file on disk.
    """
    data = {
        "version": 1,
        "source":      str(config.source),
        "destination": str(config.destination),
        "date_format": config.date_format,
        "priority":    [s.value for s in config.priority],
        "proximity_window_minutes": config.proximity_window_minutes,
        # Files already processed (source paths)
        "processed_paths": sorted(processed),
        # DeviceTimeline — needed for video proximity matching on resume
        "timeline_times":   [dt.isoformat() for dt in timeline._times],
        "timeline_devices": list(timeline._devices),
        # dest_registry — needed for duplicate detection on resume
        "dest_registry": {str(k): v for k, v in dest_registry.items()},
        # Accumulated stats so the final summary is correct
        "result": {
            "moved":               result.moved,
            "duplicates":          result.duplicates,
            "errors":              result.errors,
            "skipped":             result.skipped,
            "proximity_warnings":  result.proximity_warnings,
            "by_source":           dict(result.by_source),
            "by_device":           dict(result.by_device),
            "by_extension":        dict(result.by_extension),
        },
        "undo_log": result.undo_log,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic rename — avoids corrupt checkpoint on crash


def _load_checkpoint(path: Path) -> Optional[dict]:
    """Load and return a checkpoint dict, or None if missing / corrupt."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── Undo log ───────────────────────────────────────────────────────────────────

def _write_undo_logs(destination: Path, undo_log: list[tuple[str, str]]) -> None:
    """
    Write photosort-undo.sh (bash) and photosort-undo.bat (Windows) to *destination*.
    Each entry in *undo_log* is (original_path, dest_path).
    """
    if not undo_log:
        return

    destination.mkdir(parents=True, exist_ok=True)

    # ── bash ──────────────────────────────────────────────────────────────────
    sh_lines = [
        "#!/usr/bin/env bash",
        "# PhotoSort undo script — restores every moved file to its original location.",
        "# Generated by PhotoSort. Run once to undo the last sort.",
        "",
    ]
    for orig, dest in undo_log:
        orig_dir = str(Path(orig).parent).replace('"', '\\"')
        orig_esc = orig.replace('"', '\\"')
        dest_esc = dest.replace('"', '\\"')
        sh_lines.append(f'mkdir -p "{orig_dir}" && mv -- "{dest_esc}" "{orig_esc}"')

    sh_path = destination / UNDO_SH_FILENAME
    sh_path.write_text("\n".join(sh_lines) + "\n", encoding="utf-8")
    try:
        sh_path.chmod(sh_path.stat().st_mode | 0o111)
    except OSError:
        pass  # chmod not supported on all platforms (e.g. FAT32 on Windows)

    # ── Windows batch ─────────────────────────────────────────────────────────
    bat_lines = [
        "@echo off",
        "REM PhotoSort undo script -- restores every moved file to its original location.",
        "REM Generated by PhotoSort. Run once to undo the last sort.",
        "",
    ]
    for orig, dest in undo_log:
        orig_dir = str(Path(orig).parent)
        bat_lines.append(f'if not exist "{orig_dir}\\" md "{orig_dir}"')
        bat_lines.append(f'move /Y "{dest}" "{orig}"')

    bat_path = destination / UNDO_BAT_FILENAME
    bat_path.write_text("\r\n".join(bat_lines) + "\r\n", encoding="utf-8")


# ── Scanning ──────────────────────────────────────────────────────────────────

def scan_media_files(
    source: Path,
    on_progress: Optional[ScanProgressCallback] = None,
) -> tuple[list[Path], int]:
    """
    Return (media_files, non_media_count) for all files under *source* (recursive).
    media_files is sorted by path.
    Calls on_progress(count) after each media file found.
    """
    files: list[Path] = []
    non_media = 0
    for dirpath, _, filenames in os.walk(source):
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() in ALL_EXTENSIONS:
                files.append(p)
                if on_progress:
                    on_progress(len(files))
            else:
                non_media += 1
    return sorted(files), non_media


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

    # --- Collision ---
    # During a dry run the destination doesn't exist yet, so we cannot read
    # the "already placed" file to compare hashes.  Skip hashing entirely and
    # treat every name collision as a distinct file.  This avoids reading
    # potentially multi-GB source files for no useful result.
    if dry_run:
        final = _safe_suffix(intended_dest, dest_registry)
        dest_registry[final] = ""
        return final, False, 0

    # --- Live run: hash both files to detect true duplicates ---
    incoming_hash = sha256_file(source_path)

    existing_hash = dest_registry[intended_dest]
    if not existing_hash:
        # First time this slot was actually filled — compute the existing file's hash
        if intended_dest.exists():
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
    final = _safe_suffix(intended_dest, dest_registry)
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
    existing = [k for k in dest_registry if "duplicates" in str(k) and k.name == intended_dest.name]
    return len(existing) + 1


def _safe_suffix(path: Path, dest_registry: dict[Path, str]) -> Path:
    """Append _1, _2, … to stem until a name not in the registry or on disk is found."""
    counter = 1
    while True:
        candidate = path.with_stem(f"{path.stem}_{counter}")
        if candidate not in dest_registry and not candidate.exists():
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
    on_scan_progress: Optional[ScanProgressCallback] = None,
    pause_event: Optional[threading.Event] = None,
    on_confirm: Optional[ConfirmCallback] = None,
    checkpoint_path: Optional[Path] = None,
    resume: bool = False,
) -> SortResult:
    """
    Two-pass sort:
      Pass 1 — photos only: detect EXIF device, build DeviceTimeline.
      Pass 2 — videos only: try container metadata, then proximity match.

    Progress is reported via on_progress(current_index, total, FileRecord).
    on_scan_progress(count) fires during the initial scan phase as files are found.
    pause_event: when set (or None) the engine runs; when cleared, it blocks until resumed.
    config.workers > 1 enables concurrent file processing (good for SSDs/network shares).
    """
    result = SortResult()
    dest_registry: dict[Path, str] = {}
    timeline = DeviceTimeline()

    _registry_lock = threading.Lock()
    _timeline_lock = threading.Lock()
    _counter_lock  = threading.Lock()
    _counter       = [0]

    # ── Skip list ─────────────────────────────────────────────────────────────
    skip_patterns = _load_skip_patterns(config.source)

    all_files, non_media_count = scan_media_files(config.source, on_scan_progress)

    if skip_patterns:
        kept: list[Path] = []
        for f in all_files:
            if _matches_skip(f, config.source, skip_patterns):
                result.skipped += 1
            else:
                kept.append(f)
        all_files = kept

    # ── Confirmation gate ─────────────────────────────────────────────────────
    if on_confirm:
        total_bytes = 0
        for f in all_files:
            try:
                total_bytes += f.stat().st_size
            except OSError:
                pass
        if not on_confirm(len(all_files), non_media_count, total_bytes):
            return result  # caller declined — return empty result

    # ── Resume: restore state from checkpoint ─────────────────────────────────
    _processed: set[str] = set()   # source paths handled this run (or prior)

    if resume and checkpoint_path:
        cp = _load_checkpoint(checkpoint_path)
        if cp:
            if cp.get("source") != str(config.source) or cp.get("destination") != str(config.destination):
                raise ValueError(
                    "Checkpoint source/destination does not match current arguments.\n"
                    f"  Checkpoint source : {cp.get('source')}\n"
                    f"  Current source    : {config.source}"
                )
            _processed = set(cp.get("processed_paths", []))

            # Restore DeviceTimeline
            for iso, device in zip(cp.get("timeline_times", []), cp.get("timeline_devices", [])):
                timeline._times.append(datetime.fromisoformat(iso))
                timeline._devices.append(device)

            # Restore dest_registry
            for k, v in cp.get("dest_registry", {}).items():
                dest_registry[Path(k)] = v

            # Restore accumulated stats
            r = cp.get("result", {})
            result.moved              = r.get("moved", 0)
            result.duplicates         = r.get("duplicates", 0)
            result.errors             = r.get("errors", 0)
            result.skipped            = max(result.skipped, r.get("skipped", 0))
            result.proximity_warnings = r.get("proximity_warnings", 0)
            result.by_source          = r.get("by_source", result.by_source)
            result.by_device          = dict(r.get("by_device", {}))
            result.by_extension       = dict(r.get("by_extension", {}))
            result.undo_log           = [tuple(x) for x in cp.get("undo_log", [])]

            # Filter already-done files
            all_files = [f for f in all_files if str(f) not in _processed]

    photos, videos = _split_photos_videos(all_files)
    ordered = photos + videos
    # total_files = remaining files to process; add back already-done count
    result.total_files = len(ordered) + len(_processed)
    total = result.total_files

    # ── Log file ──────────────────────────────────────────────────────────────
    log_file   = None
    log_writer = None
    if not config.dry_run and len(ordered) > 0:
        log_path = config.destination / LOG_FILENAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Append on resume so prior entries are preserved
        open_mode = "a" if resume and checkpoint_path else "w"
        log_file   = open(log_path, open_mode, newline="", encoding="utf-8")
        log_writer = csv.writer(log_file)
        if open_mode == "w":
            log_writer.writerow([
                "timestamp", "source", "destination",
                "status", "device", "date_source", "sha256", "error",
            ])

    # ── Per-file processing ───────────────────────────────────────────────────

    _stop_requested = threading.Event()  # set on Ctrl+C to drain workers cleanly

    def _do_one(filepath: Path, is_vid: bool) -> FileRecord:
        if _stop_requested.is_set():
            # Return a minimal error record so the file is NOT added to _processed
            return FileRecord(
                source_path=filepath, dest_path=None, date=None,
                date_source=None, device=None, sha256=None,
                is_duplicate=False, duplicate_index=0,
                error="interrupted",
            )
        if pause_event:
            pause_event.wait()
        return _process_file(
            filepath, config, dest_registry, timeline, is_vid,
            _registry_lock, _timeline_lock, pause_event,
        )

    def _record(record: FileRecord, is_vid: bool) -> None:
        """Accumulate result stats, write log row, fire progress callback."""
        # Track processed files for checkpoint (only non-interrupted)
        if record.error != "interrupted":
            _processed.add(str(record.source_path))

        if record.error:
            if record.error != "interrupted":
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

        if record.error != "interrupted":
            device_key = record.device or (UNMATCHED_VIDEO_FOLDER if is_vid else MISC_FOLDER)
            result.by_device[device_key] = result.by_device.get(device_key, 0) + 1

        # ── By-extension stats (non-error, non-interrupted files only) ──────
        if not record.error:
            ext = record.source_path.suffix.lstrip(".").upper() or "NO_EXT"
            result.by_extension[ext] = result.by_extension.get(ext, 0) + 1

        # ── Undo log (live run, non-error files with a dest_path) ──────────
        if not config.dry_run and not record.error and record.dest_path:
            result.undo_log.append((str(record.source_path), str(record.dest_path)))

        if record.error != "interrupted":
            result.records.append(record)

        if log_writer and record.error != "interrupted":
            status = ("error" if record.error
                      else "duplicate" if record.is_duplicate
                      else "moved")
            log_writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                str(record.source_path),
                str(record.dest_path) if record.dest_path else "",
                status,
                record.device or "",
                record.date_source.value if record.date_source else "",
                record.sha256 or "",
                record.error or "",
            ])

        with _counter_lock:
            _counter[0] += 1
            idx = _counter[0]

        if on_progress and record.error != "interrupted":
            on_progress(idx, total, record)

    # ── Two passes ────────────────────────────────────────────────────────────
    try:
        workers = config.workers
        for pass_files, is_vid in [(photos, False), (videos, True)]:
            if not pass_files:
                continue
            if workers > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_do_one, f, is_vid): is_vid for f in pass_files}
                    for future in as_completed(futures):
                        _record(future.result(), is_vid)
            else:
                for filepath in pass_files:
                    _record(_do_one(filepath, is_vid), is_vid)
    except KeyboardInterrupt:
        _stop_requested.set()
        result.interrupted = True
        # Write checkpoint so the run can be resumed (live runs only)
        if checkpoint_path and not config.dry_run and _processed:
            _write_checkpoint(checkpoint_path, _processed, timeline, dest_registry, result, config)
    finally:
        if log_file:
            log_file.close()

    # ── Undo log files ────────────────────────────────────────────────────────
    if not config.dry_run and result.undo_log:
        _write_undo_logs(config.destination, result.undo_log)

    # ── Delete checkpoint on clean completion ─────────────────────────────────
    if not result.interrupted and checkpoint_path and checkpoint_path.exists():
        try:
            checkpoint_path.unlink()
        except OSError:
            pass

    return result


# ── Single-file processor ─────────────────────────────────────────────────────

def _process_file(
    filepath: Path,
    config: SortConfig,
    dest_registry: dict[Path, str],
    timeline: DeviceTimeline,
    is_video: bool,
    registry_lock: Optional[threading.Lock] = None,
    timeline_lock: Optional[threading.Lock] = None,
    pause_event: Optional[threading.Event] = None,
) -> FileRecord:
    prox_match   = False
    prox_delta   = None
    prox_warning = False

    try:
        dt, date_source = get_best_date(filepath, config.priority)

        if is_video:
            device = get_video_device_from_metadata(filepath)
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
            device = get_device_name(filepath)
            if device and dt:
                if timeline_lock:
                    with timeline_lock:
                        timeline.add(dt, device)
                else:
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

        # Duplicate resolution and sha lookup must be atomic under the registry lock
        if registry_lock:
            with registry_lock:
                final_path, is_dup, dup_idx = resolve_duplicate(
                    intended, filepath, dest_registry, config.destination, config.dry_run
                )
                sha = dest_registry.get(final_path) or dest_registry.get(intended) or ""
        else:
            final_path, is_dup, dup_idx = resolve_duplicate(
                intended, filepath, dest_registry, config.destination, config.dry_run
            )
            sha = dest_registry.get(final_path) or dest_registry.get(intended) or ""

        if not config.dry_run:
            # Check pause again right before the move — the slow I/O operation.
            # This ensures pause is honoured even mid-file for large transfers.
            if pause_event:
                pause_event.wait()
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
