"""
Post-sort reports:

  generate_unmatched_report  — unmatched videos with device recommendations
  generate_misc_report       — summary of misc/ and screenshots/ contents
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from photosort.constants import ALL_EXTENSIONS, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from photosort.models import SCREENSHOTS_FOLDER, UNMATCHED_VIDEO_FOLDER

# Match any YYYY/YYYY-MM/YYYY-MM-DD date path segments inside a full path
_DATE_RE = re.compile(r'(\d{4})[/\\](\d{4}-\d{2})[/\\](\d{4}-\d{2}-\d{2})')

REPORT_FILENAME           = "photosort-unmatched-report.txt"
MISC_REPORT_FILENAME      = "photosort-misc-report.txt"
DUPLICATE_REPORT_FILENAME = "photosort-duplicate-report.txt"


# ── Date path extraction ──────────────────────────────────────────────────────

def _extract_date_from_path(path: Path) -> Optional[str]:
    """Return 'YYYY-MM-DD' extracted from a path, or None."""
    m = _DATE_RE.search(str(path))
    return m.group(3) if m else None


# ── Device activity scanning ──────────────────────────────────────────────────

def _build_device_date_index(destination: Path) -> dict[str, dict[str, int]]:
    """
    Walk destination, skip unmatched-videos/.
    Return {device_name: {date_str: photo_count}}.
    """
    index: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for device_dir in destination.iterdir():
        if not device_dir.is_dir():
            continue
        if device_dir.name == UNMATCHED_VIDEO_FOLDER:
            continue
        # duplicates/ subfolder — skip for counting
        for f in device_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            date = _extract_date_from_path(f)
            if date:
                index[device_dir.name][date] += 1

    return index


# ── Report generation ─────────────────────────────────────────────────────────

def generate_unmatched_report(
    destination: Path,
    dry_run: bool = False,
) -> Optional[Path]:
    """
    Generate a plain-text report of unmatched videos with device recommendations.

    Returns the path to the written report file, or None if there are no
    unmatched videos or the destination doesn't exist.
    Does nothing on a dry run.
    """
    unmatched_dir = destination / UNMATCHED_VIDEO_FOLDER
    if not unmatched_dir.exists():
        return None

    unmatched_videos = sorted(
        f for f in unmatched_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not unmatched_videos:
        return None

    device_index = _build_device_date_index(destination)
    lines = _build_report_lines(unmatched_videos, device_index, destination, dry_run)

    report_path = destination / REPORT_FILENAME
    if not dry_run:
        report_path.write_text("\n".join(lines), encoding="utf-8")

    return report_path


def _build_report_lines(
    videos: list[Path],
    device_index: dict[str, dict[str, int]],
    destination: Path,
    dry_run: bool,
) -> list[str]:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN — report not written to disk]" if dry_run else ""

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

        if date:
            # Find devices that have photos on this exact date
            activity: list[tuple[str, int]] = [
                (device, counts[date])
                for device, counts in device_index.items()
                if date in counts
            ]
            activity.sort(key=lambda x: x[1], reverse=True)

            if activity:
                lines.append("  Devices active on this date:")
                for device, count in activity:
                    lines.append(f"    {device:<30}  {count} photo(s)")
                best_device, best_count = activity[0]
                lines.append(f"  Best guess : {best_device}  ({best_count} photo(s) that day)")
            else:
                # No exact date match — broaden to same month
                month = date[:7]  # YYYY-MM
                monthly: list[tuple[str, int]] = []
                for device, counts in device_index.items():
                    total = sum(v for k, v in counts.items() if k.startswith(month))
                    if total:
                        monthly.append((device, total))
                monthly.sort(key=lambda x: x[1], reverse=True)

                if monthly:
                    lines.append(f"  No devices active on {date} — checking same month ({month}):")
                    for device, count in monthly:
                        lines.append(f"    {device:<30}  {count} photo(s) that month")
                    best_device, best_count = monthly[0]
                    lines.append(f"  Best guess : {best_device}  ({best_count} photo(s) in {month})")
                else:
                    lines.append("  No device activity found on this date or month.")
                    lines.append("  Best guess : unknown")
        else:
            lines.append("  Could not determine date from path.")
            lines.append("  Best guess : unknown")

        lines.append("")

    lines.append("=" * 62)
    lines.append("End of report")
    return lines


# ── Misc report ───────────────────────────────────────────────────────────────

def generate_misc_report(
    destination: Path,
    dry_run: bool = False,
) -> Optional[Path]:
    """
    Generate a plain-text summary of misc/ and screenshots/ contents.
    Returns the report path, or None if both folders are empty/absent.
    """
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

    report_path = destination / MISC_REPORT_FILENAME
    if not dry_run:
        report_path.write_text("\n".join(lines), encoding="utf-8")

    return report_path


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
    mode_note = "  [DRY RUN — report not written to disk]" if dry_run else ""
    W = 62

    lines = [
        "PhotoSort — Misc & Screenshots Report",
        f"Generated  : {timestamp}{mode_note}",
        f"Destination: {destination}",
        "=" * W,
        "",
    ]

    # ── Overview ──────────────────────────────────────────────────────────────
    lines += [
        f"  misc/          {len(misc_files):>5} file(s)",
        f"  screenshots/   {len(screenshots_files):>5} file(s)",
        "",
        "=" * W,
        "",
    ]

    # ── misc/ section ─────────────────────────────────────────────────────────
    if misc_files:
        lines += [
            "  MISC/",
            "  Files that had no EXIF device info and no recognisable",
            "  screenshot/screen-recording filename pattern.",
            "",
        ]
        lines += _section_lines(misc_files, device_index, destination)
        lines.append("")

    # ── screenshots/ section ──────────────────────────────────────────────────
    if screenshots_files:
        lines += [
            "  SCREENSHOTS/",
            "  Files identified as screenshots or screen recordings",
            "  by filename pattern.",
            "",
        ]
        # For screenshots, just list by type — no device recommendation needed
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


# ── Duplicate report ─────────────────────────────────────────────────────────

def generate_duplicate_report(
    destination: Path,
    dry_run: bool = False,
) -> Optional[Path]:
    """
    Generate a plain-text report of all files routed to duplicates/ subfolders.
    Returns the report path, or None if there are no duplicates.
    """
    dup_dirs = [
        d / "duplicates"
        for d in destination.iterdir()
        if d.is_dir() and (d / "duplicates").exists()
    ]
    if not dup_dirs:
        return None

    # Collect all duplicate files: {original_name: [(Dn_path, device)]}
    groups: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for dup_root in dup_dirs:
        device = dup_root.parent.name
        for f in dup_root.rglob("*"):
            if f.is_file() and f.suffix.lower() in ALL_EXTENSIONS:
                groups[f.name].append((f, device))

    if not groups:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_note = "  [DRY RUN — report not written to disk]" if dry_run else ""
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

    report_path = destination / DUPLICATE_REPORT_FILENAME
    if not dry_run:
        report_path.write_text("\n".join(lines), encoding="utf-8")

    return report_path


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

        # Find best device for this date
        if date != "unknown":
            activity = sorted(
                ((dev, counts[date]) for dev, counts in device_index.items() if date in counts),
                key=lambda x: x[1], reverse=True,
            )
            if not activity:
                month = date[:7]
                activity = sorted(
                    (
                        (dev, sum(v for k, v in counts.items() if k.startswith(month)))
                        for dev, counts in device_index.items()
                        if any(k.startswith(month) for k in counts)
                    ),
                    key=lambda x: x[1], reverse=True,
                )
                guess_note = f"(same month {month})" if activity else ""
            else:
                guess_note = "(same day)"

            if activity:
                best_device, best_count = activity[0]
                guess = f"→ {best_device}  {best_count} photo(s) {guess_note}"
            else:
                guess = "→ unknown  (no device activity near this date)"
        else:
            guess = "→ unknown  (no date info)"

        lines.append(f"  {date}  ({len(group)} file(s))  {guess}")
        for f in group[:5]:
            ext = f.suffix.lower()
            kind = "video" if ext in VIDEO_EXTENSIONS else "photo"
            lines.append(f"    [{kind}]  {f.name}")
        if len(group) > 5:
            lines.append(f"    … and {len(group) - 5} more")

    return lines
