# PhotoSort v2 — Full Expansion Plan

## Context

The current `sort_photos.py` is a 334-line single-file CLI tool that moves Android media into `YYYY/YYYY-MM/YYYY-MM-DD` folders. The goal is to expand it into a general-purpose photo/video sorter with: device-based folder sorting (EXIF camera model), SHA256 deduplication, a customtkinter GUI, an enhanced CLI, and configurable date format + sort priority.

---

## Output Folder Structure

```
output/
├── ILCE-7M3/                          ← EXIF camera model
│   ├── duplicates/
│   │   └── 2024/2024-07/2024-07-15/
│   │       ├── D1/
│   │       │   └── IMG_001.jpg        ← 2nd copy (same SHA256)
│   │       └── D2/
│   │           └── IMG_001.jpg        ← 3rd copy
│   └── 2024/2024-07/2024-07-15/
│       └── IMG_001.jpg                ← original (first seen)
├── iPhone 15 Pro/
│   └── ...
└── misc/                              ← no EXIF device info
    ├── duplicates/
    │   └── ...
    └── 2024/2024-07/2024-07-15/
```

---

## Project Structure

```
PhotoSort/
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── CLAUDE.md
├── sort_photos.py                  # Thin shim → photosort.cli:main
├── src/
│   └── photosort/
│       ├── __init__.py
│       ├── __main__.py             # python -m photosort
│       ├── constants.py            # Extensions, regex patterns, EXIF tag IDs
│       ├── models.py               # SortConfig, FileRecord, SortResult, DateSource enum
│       ├── date_extract.py         # Date extraction (EXIF, filename, created, modified)
│       ├── device_detect.py        # EXIF camera model extraction
│       ├── hashing.py              # SHA256 file hashing
│       ├── engine.py               # Core sorting engine (shared by CLI + GUI)
│       ├── cli.py                  # argparse CLI
│       └── gui/
│           ├── __init__.py
│           ├── app.py              # Main customtkinter window
│           ├── widgets.py          # FolderPicker, PrioritySelector, DateFormatSelector
│           └── worker.py           # Threading wrapper for engine
└── tests/
    ├── conftest.py
    ├── test_date_extract.py
    ├── test_device_detect.py
    ├── test_hashing.py
    └── test_engine.py
```

---

## Module Breakdown

### `constants.py`
Extract from current code: `PHOTO_EXTENSIONS`, `VIDEO_EXTENSIONS`, `ALL_EXTENSIONS`, `FILENAME_PATTERNS`, `EXIF_DATE_TAGS`.

### `models.py`
```python
class DateSource(Enum):
    EXIF = "exif"
    FILENAME = "filename"
    CREATED = "created"       # st_birthtime (macOS/Win) — skip on Linux
    MODIFIED = "modified"

@dataclass
class SortConfig:
    source: Path
    destination: Path
    dry_run: bool = False
    date_format: str = "%Y/%Y-%m/%Y-%m-%d"
    priority: list[DateSource] = [EXIF, FILENAME, CREATED, MODIFIED]

@dataclass
class FileRecord:
    source_path, dest_path, date, date_source, device,
    sha256, is_duplicate, duplicate_index, error

@dataclass
class SortResult:
    total_files, moved, duplicates, errors,
    by_source, by_device, records: list[FileRecord]
```

### `date_extract.py`
Refactor `get_best_date` to accept a `priority: list[DateSource]` and dispatch to the matching extractor in order. Add `get_created_date` using `stat().st_birthtime` (try/except for Linux where it's unavailable).

### `device_detect.py`
Read EXIF tag 272 (Model) via Pillow's public `img.getexif()`. Sanitize for filesystem safety (strip `<>:"/\|?*`). Return `None` → engine maps to `"misc"`.

### `hashing.py`
`sha256_file(path) -> str` — read in 64KB chunks, return hex digest. **Lazy hashing**: only called when a filename collision is detected at the destination (two files would land in the same date folder with the same name). No upfront hashing of every file.

### `engine.py`
The core. Key functions:

- **`scan_media_files(source) -> list[Path]`** — recursive glob for ALL_EXTENSIONS
- **`compute_destination(filepath, dt, device, date_format, base_dest) -> Path`** — builds `<device>/<formatted_date>/<filename>`, uses `"misc"` when device is None
- **`resolve_duplicate(dest_path, filepath, dest_registry) -> (final_path, is_dup, dup_index)`** — only triggers when `dest_path` already exists in the registry (filename collision). At that point, hashes both the existing file and the new file via `sha256_file`. If hashes match → true duplicate, route to `duplicates/.../D1/`. If hashes differ → different files with same name, keep both in main folder with `_1` suffix (not a duplicate).
- **`sort_files(config, on_progress=None) -> SortResult`** — orchestrator, calls above in sequence

The `on_progress` callback `(current, total, FileRecord) -> None` decouples the engine from both CLI and GUI. CLI prints lines; GUI updates progress bar. Hashing is lazy — only performed on filename collisions, not upfront for every file.

### `cli.py`
Enhanced argparse:
- `source`, `destination` (positional)
- `--dry-run`
- `--format` (strftime hierarchy, default `%Y/%Y-%m/%Y-%m-%d`)
- `--priority` (comma-separated: `exif,filename,created,modified`)

### `gui/app.py`
customtkinter main window layout:

```
┌─────────────────────────────────────────────────────┐
│  PhotoSort                                          │
├─────────────────────────────────────────────────────┤
│  INPUT FOLDER                                       │
│  [______________________________] [Browse]          │
│  Files: 1,247 total | 1,102 media                   │
├─────────────────────────────────────────────────────┤
│  OUTPUT FOLDER                                      │
│  [______________________________] [Browse]          │
├─────────────────────────────────────────────────────┤
│  OPTIONS                                            │
│  Date Format:  [%Y/%Y-%m/%Y-%m-%d       v]          │
│  Priority:     [EXIF ↑↓] [Filename ↑↓] [Created ↑↓] [Modified ↑↓] │
│  [✓] Dry Run                                        │
├─────────────────────────────────────────────────────┤
│  [          Sort Files          ]                   │
├─────────────────────────────────────────────────────┤
│  [═══════════════════════       ] 75% (831/1102)    │
├─────────────────────────────────────────────────────┤
│  LOG                                                │
│  ┌────────────────────────────────────────────────┐ │
│  │ [1/1102] EXIF  IMG_001.jpg → ILCE-7M3/2024/.. │ │
│  │ [2/1102] DUP   IMG_003.jpg → duplicates/...   │ │
│  └────────────────────────────────────────────────┘ │
│  Output: 1,102 | Duplicates: 45 | Errors: 0        │
├─────────────────────────────────────────────────────┤
│  ERRORS (hidden if 0)                               │
│  ┌────────────────────────────────────────────────┐ │
│  │ ERROR: corrupt.jpg — cannot read EXIF          │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

- **FolderPicker** widget: entry + browse button, triggers file count scan on input folder selection
- **PrioritySelector** widget: 4 buttons with ↑↓ arrows to reorder
- **DateFormatSelector** widget: dropdown with presets + custom option
- **Confirmation dialog**: "Are you sure?" when dry run is off, shows file count
- **SortWorker**: runs `engine.sort_files` in a daemon thread, marshals progress to UI via `.after()`

---

## Implementation Phases

### Phase 1: Foundation
1. `git init`, `.gitignore`, `pyproject.toml`, `requirements.txt`
2. Create project structure with empty `__init__.py` files
3. Extract `constants.py` from current code
4. Create `models.py` (dataclasses + enums)
5. Extract and refactor `date_extract.py` — add `get_created_date`, make priority configurable

### Phase 2: New Capabilities
6. Implement `hashing.py` + tests
7. Implement `device_detect.py` + tests

### Phase 3: Core Engine
8. Implement `engine.py` — `scan_media_files`, `compute_destination`, `resolve_duplicate`, `sort_files`
9. Write `test_engine.py` with temp directory fixtures

### Phase 4: CLI
10. Implement `cli.py` with new flags
11. Update root `sort_photos.py` as thin shim
12. Manual integration test with real files

### Phase 5: GUI
13. `gui/widgets.py` — FolderPicker, PrioritySelector, DateFormatSelector
14. `gui/worker.py` — threading wrapper
15. `gui/app.py` — main window, wire everything together

### Phase 6: Polish
16. Update CLAUDE.md
17. Edge case testing (empty dirs, zero-byte files, symlinks, long paths)

---

## Verification

- **Unit tests**: `pytest tests/` — date extraction, hashing, device detection, engine logic
- **CLI integration**: `python sort_photos.py <test_source> <test_dest> --dry-run` — verify file counts match, output structure correct
- **GUI manual test**: launch GUI, select folders, run dry + live, verify progress/counts/errors
- **Dedup verification**: create duplicate files, confirm originals in main folders and copies in D1/D2
- **Cross-platform**: test `get_created_date` fallback on Linux (no st_birthtime)
- **1:1 check**: after a run, `SortResult.moved + SortResult.duplicates == input media count`

---

## Dependencies

```
Pillow>=10.0
customtkinter>=5.2
```

Dev: `pytest>=7.0`

## Key Risks

- **Hashing large files is slow**: mitigated by lazy hashing — only hash when a filename collision is detected at the destination, not upfront for every file
- **EXIF Model tag varies wildly**: use raw sanitized string, no aliasing for now
- **customtkinter thread safety**: all UI updates must go through `.after()` from the worker thread
- **`st_birthtime` unavailable on Linux**: `get_created_date` must gracefully return None
