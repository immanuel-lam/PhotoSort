# PhotoSort

Organise photos and videos from any camera, phone, or device into a clean date-and-device folder hierarchy — with a GUI or from the command line.

## Output structure

```
sorted/
├── ILCE-7M3/                        ← EXIF camera model
│   ├── 2024/2024-07/2024-07-15/
│   │   └── IMG_001.jpg
│   └── duplicates/
│       └── 2024/2024-07/2024-07-15/
│           └── D1/
│               └── IMG_001.jpg      ← exact duplicate (same SHA256)
├── iPhone-15-Pro/
│   └── 2024/2024-07/2024-07-15/
├── misc/                            ← photos with no EXIF device info
├── screenshots/                     ← screenshots & screen recordings
└── unmatched-videos/                ← videos with no device match
```

## Quick start

Download the latest release for your platform from the [Releases](../../releases) page — no Python required.

| Platform | File | Notes |
|---|---|---|
| macOS | `PhotoSort-macos-arm64.dmg` | Apple Silicon (M1/M2/M3/M4) |
| Windows — x64 | `PhotoSort-windows-x64.exe` | Intel/AMD 64-bit |
| Windows — arm64 | `PhotoSort-windows-arm64.exe` | Snapdragon/ARM devices |
| Windows — Universal | `PhotoSort-windows-universal.zip` | Contains both .exe files |

Or run directly with Python:

```bash
python main.py                    # launch GUI
python main.py ~/import ~/sorted  # CLI — add --dry-run to preview without moving
```

On first run a `.venv` is created and dependencies are installed automatically.

> **macOS note:** if the GUI fails to open, run `brew install python-tk@3.x` (replace `3.x` with your Python version).

## GUI

Launch with no arguments:

```bash
python main.py
```

1. Pick an **Input Folder** — file counts appear automatically as the folder is scanned
2. Pick an **Output Folder**
3. Adjust options (date format, sort priority, proximity window, parallel workers)
4. **Dry Run** is on by default — review the log, then uncheck to move files
5. Use **Pause / Resume** to temporarily halt a running sort

## CLI

```bash
python main.py <source> <destination> [options]
```

| Option | Default | Description |
|---|---|---|
| `--dry-run` | off | Preview without moving any files |
| `--format FMT` | `%Y/%Y-%m/%Y-%m-%d` | Folder hierarchy using strftime codes |
| `--priority LIST` | `exif,filename,created,modified` | Date source order (comma-separated) |
| `--proximity-window N` | `30` | Minutes window for video→photo device matching (`0` = off) |
| `--workers N` | `1` | Parallel threads — `1` for HDDs, `4+` for SSDs or network shares |

**Examples**

```bash
# Preview with defaults
python main.py ~/import ~/sorted --dry-run

# Year/Month only, EXIF then mtime
python main.py ~/import ~/sorted --format "%Y/%Y-%m" --priority exif,modified

# 4 parallel workers for a NAS/SMB share
python main.py /mnt/nas/photos /mnt/nas/sorted --workers 4

# Disable video proximity matching
python main.py ~/import ~/sorted --proximity-window 0
```

## How files are routed

| File | Goes to |
|---|---|
| Photo with EXIF camera model | `<model>/YYYY/YYYY-MM/YYYY-MM-DD/` |
| Photo, no EXIF, screenshot pattern | `screenshots/YYYY/…` |
| Photo, no EXIF, no pattern | `misc/YYYY/…` |
| Video with container metadata | `<model>/YYYY/…` |
| Video matched to a nearby photo | `<model>/YYYY/…` *(proximity)* |
| Video, screen recording pattern | `screenshots/YYYY/…` |
| Video, no match | `unmatched-videos/YYYY/…` |
| Filename collision, same SHA256 | `<device>/duplicates/…/D1/`, `D2/`, … |
| Filename collision, different content | `_1`, `_2` suffix in main folder |

## Date priority

PhotoSort tries date sources in order until one succeeds:

1. **EXIF** — `DateTimeOriginal` / `DateTimeDigitized` / `DateTime`
2. **Filename** — patterns like `IMG_20240715_…`, `2024-07-15_…`
3. **Created** — `st_birthtime` (macOS/Windows; skipped on Linux)
4. **Modified** — `st_mtime` (always available, last resort)

Reorder with `--priority exif,modified` etc.

## Video device detection

1. **Container metadata** — reads Make/Model atoms from MP4/MOV via hachoir
2. **Proximity match** — finds the nearest photo (by timestamp) with a known EXIF device within the configured window (default 30 min). Matches over 10 min are flagged as warnings in the log.
3. **Fallback** — `unmatched-videos/`

## Reports

After each live run, PhotoSort writes the following files to the output folder:

| File | Contents |
|---|---|
| `photosort-log.csv` | Every file processed: source, destination, status, device, date source |
| `photosort-duplicate-report.txt` | All files routed to `duplicates/` subfolders — safe to delete if not needed |
| `photosort-unmatched-report.txt` | Unmatched videos with device recommendations based on nearby photo activity |
| `photosort-misc-report.txt` | Summary of `misc/` and `screenshots/` contents with device suggestions |

## Parallel processing

By default PhotoSort processes one file at a time (`--workers 1`), which is safe for spinning hard drives. For SSDs or network shares, increasing workers reduces idle time by keeping multiple reads and moves in flight simultaneously.

```
Local HDD       --workers 1   (default — avoids random-seek contention)
Local SSD       --workers 4
NAS / SMB       --workers 4–8
```

## Building from source

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest tests/ -v
```

## Requirements

| Package | Purpose |
|---|---|
| [Pillow](https://pillow.readthedocs.io) ≥ 10.0 | EXIF reading |
| [customtkinter](https://github.com/TomSchimansky/CustomTkinter) ≥ 5.2 | GUI |
| [hachoir](https://hachoir.readthedocs.io) ≥ 3.1 | Video container metadata |

## License

MIT
