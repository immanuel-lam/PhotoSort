"""
Post-sort reports:

  generate_unmatched_report  — unmatched videos with device recommendations
  generate_misc_report       — summary of misc/ and screenshots/ contents
  generate_duplicate_report  — files routed to duplicates/ subfolders

All three functions accept an optional *records* list.  When provided the
reports are generated entirely from in-memory data (no filesystem scan),
which means they work correctly on dry runs too.  When *records* is None
the functions fall back to scanning the destination folder (live-run
behaviour from previous versions).

An optional *report_dir* overrides where the .txt files are written.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from photosort.constants import ALL_EXTENSIONS, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from photosort.models import SCREENSHOTS_FOLDER, UNMATCHED_VIDEO_FOLDER

if TYPE_CHECKING:
    from photosort.models import FileRecord

# Match any YYYY/YYYY-MM/YYYY-MM-DD date path segments inside a full path
_DATE_RE = re.compile(r'(\d{4})[/\\](\d{4}-\d{2})[/\\](\d{4}-\d{2}-\d{2})')

REPORT_FILENAME           = "photosort-unmatched-report.txt"
MISC_REPORT_FILENAME      = "photosort-misc-report.txt"
DUPLICATE_REPORT_FILENAME = "photosort-duplicate-report.txt"

# Rank for sorting special folders to the bottom of the tree (after device folders)
_SPECIAL_FOLDER_RANK: dict[str, int] = {
    "misc":                  100,
    SCREENSHOTS_FOLDER:      101,
    UNMATCHED_VIDEO_FOLDER:  102,
}


def build_file_count_tree_lines(
    destination: Path,
    records: Optional[list["FileRecord"]] = None,
) -> list[str]:
    """
    Return indented lines showing file count per top-level destination folder.

    Device folders are listed first (alphabetical), then misc / screenshots /
    unmatched-videos at the end.  When *records* is provided the counts are
    derived from dest_path (works for dry runs); otherwise the filesystem is
    scanned.
    """
    counts: dict[str, int] = defaultdict(int)

    if records is not None:
        for rec in records:
            if rec.error or not rec.dest_path:
                continue
            try:
                rel = rec.dest_path.relative_to(destination)
            except ValueError:
                continue
            if rel.parts:
                counts[rel.parts[0]] += 1
    else:
        if not destination.exists():
            return []
        for top_dir in destination.iterdir():
            if not top_dir.is_dir():
                continue
            n = sum(
                1 for f in top_dir.rglob("*")
                if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS
            )
            if n:
                counts[top_dir.name] = n

    if not counts:
        return []

    sorted_folders = sorted(
        counts.keys(),
        key=lambda x: (_SPECIAL_FOLDER_RANK.get(x, 0), x),
    )

    lines = [f"  {destination.name}/"]
    last_idx = len(sorted_folders) - 1
    for i, folder in enumerate(sorted_folders):
        connector = "└── " if i == last_idx else "├── "
        lines.append(f"  {connector}{folder}/  {counts[folder]:>5} file(s)")
    return lines


# ── Date path extraction ──────────────────────────────────────────────────────

def _extract_date_from_path(path: Path) -> Optional[str]:
    """Return 'YYYY-MM-DD' extracted from a path, or None."""
    m = _DATE_RE.search(str(path))
    return m.group(3) if m else None


def _date_from_record(rec: "FileRecord") -> Optional[str]:
    if rec.date:
        return rec.date.strftime("%Y-%m-%d")
    if rec.dest_path:
        return _extract_date_from_path(rec.dest_path)
    return None


# ── Device activity index ─────────────────────────────────────────────────────

def _build_device_date_index(destination: Path) -> dict[str, dict[str, int]]:
    """
    Walk destination, skip unmatched-videos/.
    Return {device_name: {date_str: photo_count}}.
    """
    index: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    if not destination.exists():
        return index
    for device_dir in destination.iterdir():
        if not device_dir.is_dir():
            continue
        if device_dir.name == UNMATCHED_VIDEO_FOLDER:
            continue
        for f in device_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            date = _extract_date_from_path(f)
            if date:
                index[device_dir.name][date] += 1

    return index


def _build_device_date_index_from_records(
    records: list["FileRecord"],
) -> dict[str, dict[str, int]]:
    """Same as _build_device_date_index but built from in-memory records."""
    index: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for rec in records:
        if rec.error or not rec.device:
            continue
        if rec.source_path.suffix.lower() not in PHOTO_EXTENSIONS:
            continue
        date = _date_from_record(rec)
        if date:
            index[rec.device][date] += 1
    return index


# ── Report generation ─────────────────────────────────────────────────────────

def generate_unmatched_report(
    destination: Path,
    dry_run: bool = False,
    records: Optional[list["FileRecord"]] = None,
    report_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Generate a plain-text report of unmatched videos with device recommendations.

    When *records* is provided (recommended for dry runs), the report is built
    entirely from in-memory data.  Otherwise the destination folder is scanned.
    Returns the path to the written file, or None if there are no unmatched videos.
    """
    if records is not None:
        unmatched_videos = [
            rec for rec in records
            if not rec.error
            and rec.source_path.suffix.lower() in VIDEO_EXTENSIONS
            and rec.dest_path
            and UNMATCHED_VIDEO_FOLDER in rec.dest_path.parts
        ]
        if not unmatched_videos:
            return None
        device_index = _build_device_date_index_from_records(records)
        lines = _build_report_lines_from_records(unmatched_videos, device_index, destination, dry_run)
    else:
        unmatched_dir = destination / UNMATCHED_VIDEO_FOLDER
        if not unmatched_dir.exists():
            return None
        unmatched_paths = sorted(
            f for f in unmatched_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
        )
        if not unmatched_paths:
            return None
        device_index = _build_device_date_index(destination)
        lines = _build_report_lines(unmatched_paths, device_index, destination, dry_run)

    out_dir = report_dir if report_dir else destination
    report_path = out_dir / REPORT_FILENAME
    if not dry_run or report_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")

    return report_path


def generate_misc_report(
    destination: Path,
    dry_run: bool = False,
    records: Optional[list["FileRecord"]] = None,
    report_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Generate a plain-text summary of misc/ and screenshots/ contents.
    Returns the report path, or None if both folders are empty/absent.
    """
    if records is not None:
        misc_records = [
            rec for rec in records
            if not rec.error and rec.dest_path
            and "misc" in rec.dest_path.parts
            and SCREENSHOTS_FOLDER not in rec.dest_path.parts
        ]
        screenshots_records = [
            rec for rec in records
            if not rec.error and rec.dest_path
            and SCREENSHOTS_FOLDER in rec.dest_path.parts
        ]
        if not misc_records and not screenshots_records:
            return None
        device_index = _build_device_date_index_from_records(records)
        lines = _build_misc_report_lines_from_records(
            misc_records, screenshots_records, device_index, destination, dry_run,
            all_records=records,
        )
    else:
        misc_dir        = destination / "misc"
        screenshots_dir = destination / SCREENSHOTS_FOLDER
        misc_files        = _collect_files(misc_dir)
        screenshots_files = _collect_files(screenshots_dir)
        if not misc_files and not screenshots_files:
            return None
        device_index = _build_device_date_index(destination)
        lines = _build_misc_report_lines(
            misc_files, screenshots_files, device_index, destination, dry_run
        )

    out_dir = report_dir if report_dir else destination
    report_path = out_dir / MISC_REPORT_FILENAME
    if not dry_run or report_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")

    return report_path


def generate_duplicate_report(
    destination: Path,
    dry_run: bool = False,
    records: Optional[list["FileRecord"]] = None,
    report_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Generate a plain-text report of all files routed to duplicates/ subfolders.
    Returns the report path, or None if there are no duplicates.
    """
    if records is not None:
        dup_records = [rec for rec in records if rec.is_duplicate and not rec.error]
        if not dup_records:
            return None
        lines = _build_duplicate_report_lines_from_records(dup_records, destination, dry_run)
    else:
        if not destination.exists():
            return None
        dup_dirs = [
            d / "duplicates"
            for d in destination.iterdir()
            if d.is_dir() and (d / "duplicates").exists()
        ]
        if not dup_dirs:
            return None
        groups: dict[str, list[tuple[Path, str]]] = defaultdict(list)
        for dup_root in dup_dirs:
            device = dup_root.parent.name
            for f in dup_root.rglob("*"):
                if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS:
                    groups[f.name].append((f, device))
        if not groups:
            return None
        lines = _build_duplicate_report_lines(groups, destination, dry_run)

    out_dir = report_dir if report_dir else destination
    report_path = out_dir / DUPLICATE_REPORT_FILENAME
    if not dry_run or report_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")

    return report_path


# ── Report builders (filesystem-based, legacy) ────────────────────────────────

def _build_report_lines(
    videos: list[Path],
    device_index: dict[str, dict[str, int]],
    destination: Path,
    dry_run: bool,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN]" if dry_run else ""

    lines = [
        "PhotoSort — Unmatched Video Report",
        f"Generated : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * 62,
        "",
        f"  {len(videos)} video(s) could not be matched to a device.",
        "  For each, the devices most active on that date are listed.",
        "  'Best guess' = device with the most photos taken that day.",
        "",
        "=" * 62,
        "",
    ]

    for video in videos:
        date = _extract_date_from_path(video)
        rel  = video.relative_to(destination)
        lines.append(f"  {video.name}")
        lines.append(f"  Path : {rel}")
        lines.append(f"  Date : {date or 'unknown'}")
        lines += _device_recommendation_lines(date, device_index)
        lines.append("")

    lines += ["=" * 62, "End of report"]
    return lines


def _build_duplicate_report_lines(
    groups: dict[str, list[tuple[Path, str]]],
    destination: Path,
    dry_run: bool,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN]" if dry_run else ""
    W = 62
    total_dups = sum(len(v) for v in groups.values())
    lines = [
        "PhotoSort — Duplicate Report",
        f"Generated  : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * W,
        "",
        f"  {total_dups} duplicate file(s) across {len(groups)} unique filename(s).",
        "  These are exact SHA256 matches of files already in their device folder.",
        "  Safe to delete the duplicates/ subfolders if you don't need them.",
        "",
        "=" * W,
        "",
    ]
    for name in sorted(groups):
        copies = groups[name]
        lines.append(f"  {name}  ({len(copies)} duplicate copy/copies)")
        for path, device in sorted(copies, key=lambda x: str(x[0])):
            rel = path.relative_to(destination)
            lines.append(f"    [{device}]  {rel}")
        lines.append("")
    lines += ["=" * W, "End of report"]
    return lines


# ── Report builders (records-based, used for dry runs) ───────────────────────

def _build_report_lines_from_records(
    unmatched: list["FileRecord"],
    device_index: dict[str, dict[str, int]],
    destination: Path,
    dry_run: bool,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN]" if dry_run else ""

    lines = [
        "PhotoSort — Unmatched Video Report",
        f"Generated : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * 62,
        "",
        f"  {len(unmatched)} video(s) could not be matched to a device.",
        "  For each, the devices most active on that date are listed.",
        "  'Best guess' = device with the most photos taken that day.",
        "",
        "=" * 62,
        "",
    ]

    for rec in unmatched:
        date = _date_from_record(rec)
        dest_str = str(rec.dest_path) if rec.dest_path else "?"
        lines.append(f"  {rec.source_path.name}")
        lines.append(f"  Path : {dest_str}")
        lines.append(f"  Date : {date or 'unknown'}")
        lines += _device_recommendation_lines(date, device_index)
        lines.append("")

    lines += ["=" * 62, "End of report"]
    return lines


def _build_duplicate_report_lines_from_records(
    dup_records: list["FileRecord"],
    destination: Path,
    dry_run: bool,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN]" if dry_run else ""
    W = 62

    groups: dict[str, list["FileRecord"]] = defaultdict(list)
    for rec in dup_records:
        groups[rec.source_path.name].append(rec)

    lines = [
        "PhotoSort — Duplicate Report",
        f"Generated  : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * W,
        "",
        f"  {len(dup_records)} duplicate file(s) across {len(groups)} unique filename(s).",
        "  These are exact SHA256 matches of files already in their device folder.",
        "  Safe to delete the duplicates/ subfolders if you don't need them.",
        "",
        "=" * W,
        "",
    ]

    for name in sorted(groups):
        copies = groups[name]
        lines.append(f"  {name}  ({len(copies)} duplicate copy/copies)")
        for rec in sorted(copies, key=lambda r: str(r.dest_path)):
            device = rec.device or "unknown"
            dest_str = str(rec.dest_path) if rec.dest_path else "?"
            lines.append(f"    [{device}]  {dest_str}")
        lines.append("")

    lines += ["=" * W, "End of report"]
    return lines


def _build_misc_report_lines_from_records(
    misc_records: list["FileRecord"],
    screenshots_records: list["FileRecord"],
    device_index: dict[str, dict[str, int]],
    destination: Path,
    dry_run: bool,
    all_records: Optional[list["FileRecord"]] = None,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN]" if dry_run else ""
    W = 62

    tree_lines = build_file_count_tree_lines(destination, all_records)
    lines = [
        "PhotoSort — Misc & Screenshots Report",
        f"Generated  : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * W,
        "",
    ] + tree_lines + [
        "",
        "=" * W,
        "",
    ]

    if misc_records:
        lines += [
            "  MISC/",
            "  Files that had no EXIF device info and no recognisable",
            "  screenshot/screen-recording filename pattern.",
            "",
        ]
        by_date: dict[str, list["FileRecord"]] = defaultdict(list)
        for rec in misc_records:
            by_date[_date_from_record(rec) or "unknown"].append(rec)
        for date in sorted(by_date):
            group = by_date[date]
            rec_lines = _device_recommendation_lines(date if date != "unknown" else None, device_index)
            guess = rec_lines[0].strip() if rec_lines else "→ unknown"
            lines.append(f"  {date}  ({len(group)} file(s))  {guess}")
            for rec in group[:5]:
                ext = rec.source_path.suffix.lower()
                kind = "video" if ext in VIDEO_EXTENSIONS else "photo"
                lines.append(f"    [{kind}]  {rec.source_path.name}")
            if len(group) > 5:
                lines.append(f"    … and {len(group) - 5} more")
        lines.append("")

    if screenshots_records:
        lines += [
            "  SCREENSHOTS/",
            "  Files identified as screenshots or screen recordings",
            "  by filename pattern.",
            "",
        ]
        photo_count = sum(1 for r in screenshots_records if r.source_path.suffix.lower() in PHOTO_EXTENSIONS)
        video_count = sum(1 for r in screenshots_records if r.source_path.suffix.lower() in VIDEO_EXTENSIONS)
        lines.append(f"  {photo_count} screenshot(s)   {video_count} screen recording(s)")
        lines.append("")

        by_date2: dict[str, list["FileRecord"]] = defaultdict(list)
        for rec in screenshots_records:
            by_date2[_date_from_record(rec) or "unknown"].append(rec)
        for date in sorted(by_date2):
            group = by_date2[date]
            lines.append(f"  {date}  ({len(group)} file(s))")
            for rec in group[:5]:
                lines.append(f"    {rec.source_path.name}")
            if len(group) > 5:
                lines.append(f"    … and {len(group) - 5} more")
        lines.append("")

    lines += ["=" * W, "End of report"]
    return lines


# ── Shared helpers ────────────────────────────────────────────────────────────

def _device_recommendation_lines(
    date: Optional[str],
    device_index: dict[str, dict[str, int]],
) -> list[str]:
    """Return 1-3 lines with device activity and best-guess recommendation."""
    if not date:
        return ["  Best guess : unknown  (no date info)"]

    activity: list[tuple[str, int]] = [
        (device, counts[date])
        for device, counts in device_index.items()
        if date in counts
    ]
    activity.sort(key=lambda x: x[1], reverse=True)

    if activity:
        lines = ["  Devices active on this date:"]
        for device, count in activity:
            lines.append(f"    {device:<30}  {count} photo(s)")
        best_device, best_count = activity[0]
        lines.append(f"  Best guess : {best_device}  ({best_count} photo(s) that day)")
        return lines

    month = date[:7]
    monthly: list[tuple[str, int]] = []
    for device, counts in device_index.items():
        total = sum(v for k, v in counts.items() if k.startswith(month))
        if total:
            monthly.append((device, total))
    monthly.sort(key=lambda x: x[1], reverse=True)

    if monthly:
        lines = [f"  No devices active on {date} — checking same month ({month}):"]
        for device, count in monthly:
            lines.append(f"    {device:<30}  {count} photo(s) that month")
        best_device, best_count = monthly[0]
        lines.append(f"  Best guess : {best_device}  ({best_count} photo(s) in {month})")
        return lines

    return [
        "  No device activity found on this date or month.",
        "  Best guess : unknown",
    ]


def _collect_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS
    )


def _build_misc_report_lines(
    misc_files: list[Path],
    screenshots_files: list[Path],
    device_index: dict[str, dict[str, int]],
    destination: Path,
    dry_run: bool,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN]" if dry_run else ""
    W = 62

    tree_lines = build_file_count_tree_lines(destination)
    lines = [
        "PhotoSort — Misc & Screenshots Report",
        f"Generated  : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * W,
        "",
    ] + tree_lines + [
        "",
        "=" * W,
        "",
    ]

    if misc_files:
        lines += [
            "  MISC/",
            "  Files that had no EXIF device info and no recognisable",
            "  screenshot/screen-recording filename pattern.",
            "",
        ]
        lines += _section_lines(misc_files, device_index, destination)
        lines.append("")

    if screenshots_files:
        lines += [
            "  SCREENSHOTS/",
            "  Files identified as screenshots or screen recordings",
            "  by filename pattern.",
            "",
        ]
        photo_count = sum(1 for f in screenshots_files if f.suffix.lower() in PHOTO_EXTENSIONS)
        video_count = sum(1 for f in screenshots_files if f.suffix.lower() in VIDEO_EXTENSIONS)
        lines.append(f"  {photo_count} screenshot(s)   {video_count} screen recording(s)")
        lines.append("")

        by_date: dict[str, list[Path]] = defaultdict(list)
        for f in screenshots_files:
            date = _extract_date_from_path(f) or "unknown"
            by_date[date].append(f)

        for date in sorted(by_date):
            lines.append(f"  {date}  ({len(by_date[date])} file(s))")
            for f in by_date[date][:5]:
                lines.append(f"    {f.name}")
            if len(by_date[date]) > 5:
                lines.append(f"    … and {len(by_date[date]) - 5} more")
        lines.append("")

    lines += ["=" * W, "End of report"]
    return lines


def _section_lines(
    files: list[Path],
    device_index: dict[str, dict[str, int]],
    destination: Path,
) -> list[str]:
    """Per-file lines with device recommendation, grouped by date."""
    by_date: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        date = _extract_date_from_path(f) or "unknown"
        by_date[date].append(f)

    lines = []
    for date in sorted(by_date):
        group = by_date[date]
        rec_lines = _device_recommendation_lines(date if date != "unknown" else None, device_index)
        guess = rec_lines[0].strip() if rec_lines else "→ unknown"
        lines.append(f"  {date}  ({len(group)} file(s))  {guess}")
        for f in group[:5]:
            ext = f.suffix.lower()
            kind = "video" if ext in VIDEO_EXTENSIONS else "photo"
            lines.append(f"    [{kind}]  {f.name}")
        if len(group) > 5:
            lines.append(f"    … and {len(group) - 5} more")

    return lines
