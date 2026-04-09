# PhotoSort v2 — CLAUDE.md

## What this project is

A general-purpose photo and video organiser. Moves media files from a source folder into a
device-based + date-based hierarchy:

```
output/
├── ILCE-7M3/                     ← EXIF camera model (tag 272)
│   ├── duplicates/
│   │   └── 2024/2024-07/2024-07-15/
│   │       └── D1/               ← 2nd copy (same SHA256 + same filename)
│   └── 2024/2024-07/2024-07-15/
│       └── IMG_001.jpg
├── iPhone-15-Pro/
│   └── ...
├── misc/                         ← no EXIF device, not a screenshot
├── screenshots/                  ← Screenshot_*, Screen Recording *, etc.
└── unmatched-videos/             ← videos with no device match at all
```

## How to run

**Simplest (auto-installs deps on first run):**
```bash
python3 main.py           # → launches GUI
python3 main.py <src> <dst> [--dry-run]  # → CLI mode
```

**macOS / Linux (manual venv):**
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python sort_photos.py <source> <destination> --dry-run
.venv/bin/python -m photosort.gui.app
.venv/bin/pytest tests/ -v
```

**Windows:**
```powershell
python -m venv .venv && .venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python sort_photos.py <source> <destination> --dry-run
.venv\Scripts\python -m photosort.gui.app
.venv\Scripts\pytest tests\ -v
```

> **macOS note:** Homebrew Python 3.14 ships without Tk. Run `brew install python-tk@3.14` once if the GUI fails to launch.

## CLI flags

| Flag | Default | Description |
|---|---|---|
| `--dry-run` | off | Preview without moving files |
| `--format FMT` | `%Y/%Y-%m/%Y-%m-%d` | strftime hierarchy separated by `/` |
| `--priority LIST` | `exif,filename,created,modified` | Date source priority order |
| `--proximity-window N` | `30` | Minutes window for video→photo device matching (0 = off) |

## Project structure

```
PhotoSort/
├── main.py                 # Zero-setup entry point (auto-installs deps + venv)
├── sort_photos.py          # Thin shim → photosort.cli:main
├── src/photosort/
│   ├── constants.py        # Extensions, regex patterns, EXIF tag IDs
│   ├── models.py           # SortConfig, FileRecord, SortResult, DateSource
│   ├── date_extract.py     # Date extraction with configurable priority
│   ├── device_detect.py    # EXIF camera model (tag 272) → folder name
│   ├── screenshot_detect.py # Screenshot/screen-recording filename detection
│   ├── hashing.py          # SHA256 lazy hashing (only on filename collision)
│   ├── video_detect.py     # Video device: container metadata + proximity match
│   ├── engine.py           # Core sort engine (shared by CLI + GUI)
│   ├── report.py           # Post-sort reports (unmatched videos, misc summary)
│   ├── cli.py              # argparse CLI
│   └── gui/
│       ├── app.py          # customtkinter main window
│       ├── widgets.py      # FolderPicker, PrioritySelector, DateFormatSelector,
│       │                   #   ProximityWindowSelector
│       └── worker.py       # Background thread wrapper
└── tests/                  # pytest suite (33 tests)
```

## Folder routing logic

| Condition | Destination |
|---|---|
| Photo with EXIF camera model | `<model>/YYYY/YYYY-MM/YYYY-MM-DD/` |
| Photo without EXIF, screenshot pattern | `screenshots/YYYY/...` |
| Photo without EXIF, no pattern | `misc/YYYY/...` |
| Video with container metadata (MP4/MOV) | `<model>/YYYY/...` |
| Video matched by proximity to a photo | `<model>/YYYY/...` (PROX tag in log) |
| Video, screen recording pattern | `screenshots/YYYY/...` |
| Video, no match at all | `unmatched-videos/YYYY/...` |
| Any file: filename collision, same hash | `<device>/duplicates/.../D1/`, `D2/`, … |
| Any file: filename collision, diff hash | `_1`, `_2` suffix in main folder |

## Screenshot detection patterns

Detected by filename prefix (case-insensitive):
- `Screenshot_*` — Android (Samsung, Pixel, MIUI)
- `Screenshot YYYY*` — macOS
- `Screenshot (*).png` — Windows Snipping Tool
- `Screen_recording_*` — Samsung
- `Screen Recording YYYY*` — macOS / iOS
- `screenrecord_*` — generic Android
- `ScreenRecorder_*` — Xiaomi / MIUI
- `record_screen_*` — some Android ROMs

## Video device detection

1. **Container metadata** — reads `producer`/`model`/`device` atoms from MP4/MOV via `hachoir3`
2. **Proximity match** — finds nearest photo (by timestamp) with a known EXIF device within the configurable window (default 30 min). Matches >10 min flagged as warnings in orange.
3. **Fallback** — `unmatched-videos/`

## Post-sort reports

Both written to the destination folder after every sort (skipped on dry run):

| File | Contents |
|---|---|
| `photosort-unmatched-report.txt` | Each unmatched video + device recommendation based on photo activity on the same date/month |
| `photosort-misc-report.txt` | Summary of `misc/` and `screenshots/` contents, grouped by date, with device suggestions for misc files |

## Key design decisions

- **Lazy hashing**: SHA256 only computed on filename collision, not upfront.
- **Two-pass engine**: photos processed first to build `DeviceTimeline`; videos second so proximity matching has a complete timeline.
- **Proximity warnings**: gap >10 min = orange in GUI log, listed in CLI summary and misc report.
- **`on_progress` callback**: engine emits `(current, total, FileRecord)` — no UI coupling in the engine.
- **Thread safety**: GUI runs `SortWorker` in a daemon thread; all UI updates via `.after(0, ...)`.
- **Cross-platform fonts**: `Consolas` (Windows), `Menlo` (macOS), `DejaVu Sans Mono` (Linux).
- **Cross-platform moves**: `_move_file()` falls back to `copy2` + `unlink` for cross-drive moves on Windows.
- **CLI encoding**: emoji/box chars only used when `stdout.encoding` is UTF-8; plain ASCII fallback otherwise.

## Dependencies

| Package | Purpose |
|---|---|
| `Pillow>=10.0` | EXIF reading, device detection |
| `customtkinter>=5.2` | GUI |
| `hachoir>=3.1` | Video container metadata |
| `pytest>=7.0` | Dev/tests only |
