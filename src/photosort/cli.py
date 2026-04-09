"""Enhanced argparse CLI for PhotoSort."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photosort.engine import MISC_FOLDER, sort_files
from photosort.models import (
    DATE_FORMAT_PRESETS,
    DEFAULT_DATE_FORMAT,
    DEFAULT_PRIORITY,
    DateSource,
    FileRecord,
    SortConfig,
)

try:
    from photosort.date_extract import PILLOW_AVAILABLE
except ImportError:
    PILLOW_AVAILABLE = False

from photosort.video_detect import CONFIDENT_MINUTES as _CONFIDENT_MINUTES

# Use plain ASCII on Windows unless the terminal reports UTF-8
_UTF8 = sys.stdout.encoding is not None and "utf" in sys.stdout.encoding.lower()

if _UTF8:
    _ICON = {
        DateSource.EXIF:     '📷',
        DateSource.FILENAME: '🏷 ',
        DateSource.CREATED:  '📅',
        DateSource.MODIFIED: '🕐',
    }
    _DUP_ICON = '♻  '
    _ERR_ICON  = '✗  '
    _BAR_HEAVY = '═'
    _BAR_LIGHT = '─'
else:
    _ICON = {
        DateSource.EXIF:     '[EXIF]    ',
        DateSource.FILENAME: '[FILE]    ',
        DateSource.CREATED:  '[CREATED] ',
        DateSource.MODIFIED: '[MTIME]   ',
    }
    _DUP_ICON = '[DUP]  '
    _ERR_ICON  = '[ERR]  '
    _BAR_HEAVY = '='
    _BAR_LIGHT = '-'


# ── Argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    preset_list = "\n  ".join(f"{fmt!r} — {label}" for fmt, label in DATE_FORMAT_PRESETS)
    priority_values = ", ".join(s.value for s in DateSource)

    parser = argparse.ArgumentParser(
        prog="photosort",
        description="Sort photos and videos into organised date/device folders.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Date format presets:
  {preset_list}

Priority values (comma-separated, in order of preference):
  {priority_values}

Examples:
  # Dry-run with defaults:
  photosort ~/import ~/sorted --dry-run

  # Year/Month only, EXIF then mtime:
  photosort ~/import ~/sorted --format "%Y/%Y-%m" --priority exif,modified

  # Launch the GUI instead:
  photosort-gui
""",
    )

    parser.add_argument("source",      help="Input folder (searched recursively)")
    parser.add_argument("destination", help="Output root folder")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without moving any files",
    )
    parser.add_argument(
        "--format",
        default=DEFAULT_DATE_FORMAT,
        metavar="FMT",
        help="Date folder hierarchy using strftime codes, /-separated. Default: %(default)r",
    )
    parser.add_argument(
        "--priority",
        default=",".join(s.value for s in DEFAULT_PRIORITY),
        metavar="LIST",
        help=f"Comma-separated date source priority. Default: {','.join(s.value for s in DEFAULT_PRIORITY)}",
    )
    parser.add_argument(
        "--proximity-window",
        type=int,
        default=30,
        metavar="MINUTES",
        help=(
            "Match videos to the nearest photo's device if within this many minutes. "
            "0 = disabled. Matches beyond 10 min are flagged as possible false matches. "
            "Default: 30"
        ),
    )
    return parser


def _parse_priority(raw: str) -> list[DateSource]:
    """Parse and validate a comma-separated priority string."""
    valid = {s.value: s for s in DateSource}
    result = []
    for token in raw.split(","):
        token = token.strip().lower()
        if token not in valid:
            raise argparse.ArgumentTypeError(
                f"Unknown priority value {token!r}. "
                f"Choose from: {', '.join(valid)}"
            )
        s = valid[token]
        if s not in result:
            result.append(s)
    # Always ensure MODIFIED is at the end as a backstop
    if DateSource.MODIFIED not in result:
        result.append(DateSource.MODIFIED)
    return result


# ── Progress callback ─────────────────────────────────────────────────────────

def _make_progress_callback(total: int):
    pad = len(str(total))

    def callback(current: int, _total: int, record: FileRecord) -> None:
        if record.error:
            print(f"[{current:>{pad}}/{total}] {_ERR_ICON} ERROR {record.source_path.name}: {record.error}")
            return

        if record.is_duplicate:
            icon = _DUP_ICON
            tag  = f"DUP  D{record.duplicate_index}"
        elif record.proximity_warning:
            icon = "⚠  " if _UTF8 else "[!]"
            mins = int(record.proximity_delta.total_seconds() // 60) if record.proximity_delta else "?"
            tag  = f"PROX? {mins}min"
        elif record.proximity_match:
            icon = "🎬 " if _UTF8 else "[V]"
            mins = int(record.proximity_delta.total_seconds() // 60) if record.proximity_delta else "?"
            tag  = f"PROX  {mins}min"
        else:
            icon = _ICON.get(record.date_source, '  ')
            tag  = (record.date_source.value if record.date_source else "?").upper().ljust(8)

        hash_snippet = f"  [{record.sha256[:8]}…]" if record.sha256 else ""
        dest_str = str(record.dest_path) if record.dest_path else "?"
        print(
            f"[{current:>{pad}}/{total}] {icon} {tag}  {record.source_path.name}"
            f"\n{'':>{pad+2}}   → {dest_str}{hash_snippet}"
        )

    return callback


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    source      = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()

    # Sanity checks
    if not source.exists():
        parser.error(f"Source not found: {source}")
    if not source.is_dir():
        parser.error(f"Source is not a folder: {source}")
    if destination == source:
        parser.error("Source and destination must be different folders.")

    try:
        priority = _parse_priority(args.priority)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    if not PILLOW_AVAILABLE:
        warn = "⚠️ " if _UTF8 else "[!]"
        print(
            f"{warn} Pillow not installed — EXIF reading disabled.\n"
            "   pip install Pillow\n"
            "   Falling back to filename/filesystem date detection.\n",
            file=sys.stderr,
        )

    mode = "DRY RUN (no files will be moved)" if args.dry_run else "LIVE RUN"
    print(f"\n{_BAR_HEAVY*62}")
    print(f"  PhotoSort  [{mode}]")
    print(f"{_BAR_HEAVY*62}")
    print(f"  Source      : {source}")
    print(f"  Destination : {destination}")
    prox = args.proximity_window
    print(f"  Format      : {args.format}")
    print(f"  Priority    : {' → '.join(s.value for s in priority)}")
    print(f"  Proximity   : {f'{prox} min (warnings >10 min)' if prox > 0 else 'disabled'}")
    _ok  = "✓" if _UTF8 else "OK"
    _no  = "✗" if _UTF8 else "--"
    print(f"  Pillow/EXIF : {_ok + ' enabled' if PILLOW_AVAILABLE else _no + ' disabled'}")
    print(f"{_BAR_LIGHT*62}\n")

    config = SortConfig(
        source=source,
        destination=destination,
        dry_run=args.dry_run,
        date_format=args.format,
        priority=priority,
        proximity_window_minutes=prox,
    )

    # Scan count first
    from photosort.engine import scan_media_files
    media_files = scan_media_files(source)
    total = len(media_files)
    if total == 0:
        print("No media files found in source folder.")
        return
    print(f"Found {total} media file(s).\n")

    result = sort_files(config, on_progress=_make_progress_callback(total))

    # Summary
    action = "Would move" if args.dry_run else "Moved"
    print(f"\n{_BAR_HEAVY*62}")
    print(f"  {action}: {result.moved} file(s)  |  Duplicates: {result.duplicates}  |  Errors: {result.errors}")
    _warn = "⚠ " if _UTF8 else "[!]"
    if result.proximity_warnings:
        print(f"  {_warn}  {result.proximity_warnings} video(s) matched by proximity with low confidence (>10 min gap)")
    print(f"  Date sources — " + "  ".join(
        f"{k.upper()}: {v}" for k, v in result.by_source.items() if v
    ))
    if result.by_device:
        print(f"  Devices — " + "  ".join(
            f"{k}: {v}" for k, v in sorted(result.by_device.items())
        ))
    if result.proximity_warnings:
        print(f"\n  Possible false matches (proximity >{_CONFIDENT_MINUTES} min):")
        for rec in result.records:
            if rec.proximity_warning and rec.dest_path:
                mins = int(rec.proximity_delta.total_seconds() // 60) if rec.proximity_delta else "?"
                print(f"    {_warn}  {rec.source_path.name}  →  {rec.device}  ({mins} min gap)")
    if result.errors:
        print(f"\n  Errors:")
        _err = "✗" if _UTF8 else "x"
        for rec in result.records:
            if rec.error:
                print(f"    {_err} {rec.source_path.name}: {rec.error}")

    # Unmatched video report
    from photosort.report import (
        generate_unmatched_report, REPORT_FILENAME,
        generate_misc_report, MISC_REPORT_FILENAME,
    )
    report_path = generate_unmatched_report(destination, dry_run=args.dry_run)
    if report_path:
        label = f"[DRY RUN] Would write → {REPORT_FILENAME}" if args.dry_run else str(report_path)
        print(f"\n  Unmatched video report → {label}")

    misc_report_path = generate_misc_report(destination, dry_run=args.dry_run)
    if misc_report_path:
        label = f"[DRY RUN] Would write → {MISC_REPORT_FILENAME}" if args.dry_run else str(misc_report_path)
        print(f"  Misc report           → {label}")

    print(f"{_BAR_HEAVY*62}\n")


if __name__ == "__main__":
    main()
