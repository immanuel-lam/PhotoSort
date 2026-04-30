"""Enhanced argparse CLI for PhotoSort."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from photosort.engine import (
    CHECKPOINT_FILENAME,
    MISC_FOLDER,
    UNDO_BAT_FILENAME,
    UNDO_SH_FILENAME,
    sort_files,
)
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
    parser.add_argument(
        "--date-first",
        action="store_true",
        help=(
            "Group by date first, then device — i.e. <YYYY>/<YYYY-MM>/<YYYY-MM-DD>/<device>/. "
            "Default layout is <device>/<YYYY>/<YYYY-MM>/<YYYY-MM-DD>/."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Parallel worker threads. 1 = single-threaded (default, safe for HDDs). "
            "4+ recommended for SSDs or network shares (SMB/NAS)."
        ),
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        metavar="DIR",
        help=(
            "Write post-sort reports to this folder instead of the destination. "
            "Also causes reports to be written during --dry-run."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "After scanning, print file count and total size then require "
            "typing YES before proceeding. Works for both dry and live runs."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume an interrupted run using the checkpoint file saved on Ctrl+C. "
            "Already-processed files are skipped; state is restored exactly."
        ),
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        metavar="FILE",
        help=(
            f"Path to the checkpoint file (default: <destination>/{CHECKPOINT_FILENAME}). "
            "Useful when the destination is read-only."
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


# ── Confirm callback ──────────────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    for unit, threshold in (("TB", 1_000_000_000_000), ("GB", 1_000_000_000), ("MB", 1_000_000)):
        if n >= threshold:
            return f"{n / threshold:.1f} {unit}"
    return f"{n / 1_000:.1f} KB"


def _make_confirm_callback(dry_run: bool):
    """
    Returns an on_confirm(media_count, non_media_count, total_bytes) -> bool callback.
    Prints a summary and requires the user to type YES to proceed.
    """
    def callback(media_count: int, non_media_count: int, total_bytes: int) -> bool:
        mode = "DRY RUN" if dry_run else "LIVE RUN"
        size_str = _fmt_size(total_bytes)
        print(f"\n  Scan complete: {media_count:,} media file(s)  |  {non_media_count:,} non-media  |  {size_str}")
        if not dry_run:
            print("  Files will be MOVED from source. This cannot be undone without the undo script.")
        print(f"\n  Type YES to continue [{mode}], or anything else to abort: ", end="", flush=True)
        answer = input().strip()
        if answer.upper() != "YES":
            print("  Aborted.")
            return False
        return True
    return callback


# ── Sticky progress bar ───────────────────────────────────────────────────────

_FILL  = '█' if _UTF8 else '#'
_EMPTY = '░' if _UTF8 else '-'


class _StickyBar:
    """
    Maintains a progress bar pinned to the last terminal line.
    Log lines scroll above it; the bar is redrawn after every print.
    """

    def __init__(self):
        self._drawn = False

    def _width(self) -> int:
        return shutil.get_terminal_size((80, 24)).columns

    def clear(self):
        """Erase the bar line so normal output can scroll past it."""
        if self._drawn:
            w = self._width()
            sys.stdout.write(f"\r{' ' * w}\r")
            sys.stdout.flush()
            self._drawn = False

    def draw(self, current: int, total: int):
        """(Re)draw the bar on the current line without a trailing newline."""
        if total == 0:
            return
        pct  = current / total
        suffix = f" {pct*100:5.1f}%  {current:,}/{total:,}"
        w    = self._width()
        bar_w = max(4, w - len(suffix) - 2)  # 2 for [ ]
        filled = int(bar_w * pct)
        bar  = f"[{_FILL * filled}{_EMPTY * (bar_w - filled)}]{suffix}"
        sys.stdout.write(f"\r{bar[:w]}")
        sys.stdout.flush()
        self._drawn = True

    def print_line(self, line: str):
        """Clear bar → print a log line → redraw bar."""
        self.clear()
        print(line)

    def finish(self):
        """Clear the bar permanently (call before summary output)."""
        self.clear()


# ── Progress callback ─────────────────────────────────────────────────────────

def _make_progress_callback(total_ref, bar: _StickyBar):
    """
    total_ref is either an int or a [int] list (updated live during scan).
    Using a list lets the CLI update the denominator once scanning finishes.
    """

    def callback(current: int, _total: int, record: FileRecord) -> None:
        total = total_ref[0] if isinstance(total_ref, list) else total_ref
        pad   = len(str(total))
        if record.error:
            bar.print_line(f"[{current:>{pad}}/{total}] {_ERR_ICON} ERROR {record.source_path.name}: {record.error}")
            bar.draw(current, total)
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
        bar.print_line(
            f"[{current:>{pad}}/{total}] {icon} {tag}  {record.source_path.name}"
            f"\n{'':>{pad+2}}   → {dest_str}{hash_snippet}"
        )
        bar.draw(current, total)

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

    report_dir      = Path(args.report_dir).expanduser().resolve() if args.report_dir else None
    checkpoint_path = (
        Path(args.checkpoint).expanduser().resolve()
        if args.checkpoint
        else destination / CHECKPOINT_FILENAME
    )

    mode = "DRY RUN (no files will be moved)" if args.dry_run else "LIVE RUN"
    if args.resume:
        mode += " [RESUMING]"
    print(f"\n{_BAR_HEAVY*62}")
    print(f"  PhotoSort  [{mode}]")
    print(f"{_BAR_HEAVY*62}")
    print(f"  Source      : {source}")
    print(f"  Destination : {destination}")
    if report_dir:
        print(f"  Report dir  : {report_dir}")
    prox = args.proximity_window
    print(f"  Format      : {args.format}")
    print(f"  Layout      : {'date / device' if args.date_first else 'device / date'}")
    print(f"  Priority    : {' → '.join(s.value for s in priority)}")
    print(f"  Proximity   : {f'{prox} min (warnings >10 min)' if prox > 0 else 'disabled'}")
    workers = max(1, args.workers)
    print(f"  Workers     : {workers}")
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
        workers=workers,
        device_first=not args.date_first,
    )

    # Scan with live progress, then sort
    def _on_scan(count: int) -> None:
        print(f"\r  Scanning… {count:,} files found", end="", flush=True)

    # _total is filled in once the scan completes; progress callback reads it live
    _total = [0]

    def _on_scan(count: int) -> None:
        print(f"\r  Scanning… {count:,} files found", end="", flush=True)
        _total[0] = count

    confirm_cb = _make_confirm_callback(args.dry_run) if args.confirm else None

    bar = _StickyBar()

    result = sort_files(
        config,
        on_progress=_make_progress_callback(_total, bar),
        on_scan_progress=_on_scan,
        on_confirm=confirm_cb,
        checkpoint_path=checkpoint_path,
        resume=args.resume,
    )
    bar.finish()
    total = result.total_files
    print(f"\r  Found {total:,} media file(s).{' ' * 20}")  # overwrite scan line
    print()

    if result.interrupted:
        _warn = "⚠ " if _UTF8 else "[!]"
        print(f"  {_warn} Interrupted — checkpoint saved to {checkpoint_path}")
        print(f"  Resume with: --resume (add --checkpoint {checkpoint_path} if needed)\n")

    if total == 0:
        print("No media files found in source folder.")
        return

    # Summary
    action = "Would move" if args.dry_run else "Moved"
    print(f"\n{_BAR_HEAVY*62}")
    print(f"  {action}: {result.moved} file(s)  |  Duplicates: {result.duplicates}  |  Errors: {result.errors}")
    if result.skipped:
        _skip_icon = "⊘ " if _UTF8 else "--"
        print(f"  {_skip_icon} Skipped: {result.skipped} file(s) (matched .photosort-skip patterns)")
    _warn = "⚠ " if _UTF8 else "[!]"
    if result.proximity_warnings:
        print(f"  {_warn}  {result.proximity_warnings} video(s) matched by proximity with low confidence (>10 min gap)")
    if result.by_extension:
        parts = sorted(result.by_extension.items(), key=lambda x: -x[1])
        ext_str = ", ".join(f"{count} {ext}" for ext, count in parts)
        print(f"  {ext_str} sorted")
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

    # Post-sort reports
    from photosort.report import (
        generate_unmatched_report, REPORT_FILENAME,
        generate_misc_report, MISC_REPORT_FILENAME,
        generate_duplicate_report, DUPLICATE_REPORT_FILENAME,
    )
    # Pass result.records so reports work on dry runs too.
    # Pass report_dir when --report-dir was specified.
    _recs = result.records

    report_path = generate_unmatched_report(
        destination, dry_run=args.dry_run, records=_recs, report_dir=report_dir
    )
    if report_path:
        print(f"\n  Unmatched video report → {report_path}")

    dup_report_path = generate_duplicate_report(
        destination, dry_run=args.dry_run, records=_recs, report_dir=report_dir
    )
    if dup_report_path:
        print(f"  Duplicate report       → {dup_report_path}")

    misc_report_path = generate_misc_report(
        destination, dry_run=args.dry_run, records=_recs, report_dir=report_dir
    )
    if misc_report_path:
        print(f"  Misc report           → {misc_report_path}")

    if not args.dry_run and result.undo_log:
        print(f"  Undo scripts          → {UNDO_SH_FILENAME}  /  {UNDO_BAT_FILENAME}")
    elif args.dry_run and result.total_files > 0:
        print(f"  Undo scripts          → [DRY RUN] Would write → {UNDO_SH_FILENAME}  /  {UNDO_BAT_FILENAME}")

    print(f"{_BAR_HEAVY*62}\n")


if __name__ == "__main__":
    main()
