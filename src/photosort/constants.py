"""Shared constants: file extensions, filename patterns, EXIF tag IDs."""

import re

# ── Supported extensions ──────────────────────────────────────────────────────
PHOTO_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.heic', '.heif',
    '.raw', '.dng', '.cr2', '.nef', '.arw',
    '.tiff', '.tif', '.bmp', '.webp',
}
VIDEO_EXTENSIONS = {
    '.mp4', '.mov', '.avi', '.mkv',
    '.3gp', '.m4v', '.wmv', '.flv',
}
ALL_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

# ── Camera filename date patterns ─────────────────────────────────────────────
# Each pattern must yield either:
#   (date_str, time_str)  where date_str is YYYYMMDD and time_str is HHMMSS
#   (date_str,)           where date_str is YYYYMMDD (time ignored)
FILENAME_PATTERNS = [
    # IMG_20240715_083012.jpg  |  VID_20240715_091500.mp4  |  PXL_20240715_083012345.jpg
    re.compile(r'(?:IMG|VID|MVIMG|PXL|PANO|BURST|SAVE)_(\d{8})_(\d{6})'),
    # Screenshot_20240715-083012_Chrome.jpg  (Samsung)
    re.compile(r'Screenshot_(\d{8})-(\d{6})'),
    # Screenshot_2024-07-15-08-30-12.jpg  (MIUI / stock Android)
    re.compile(r'Screenshot_(\d{4}-\d{2}-\d{2})-(\d{2}-\d{2}-\d{2})'),
    # 20240715_083012.jpg  (bare date-time filename)
    re.compile(r'^(\d{8})_(\d{6})'),
    # 2024-07-15 08.30.12.jpg  (Google Photos export)
    re.compile(r'(\d{4}-\d{2}-\d{2})\s(\d{2}\.\d{2}\.\d{2})'),
    # WhatsApp Image 2024-07-15 at 08.30.12.jpg
    re.compile(r'(\d{4}-\d{2}-\d{2})\sat\s(\d{2}\.\d{2}\.\d{2})'),
]

# ── EXIF tag IDs (decimal) ────────────────────────────────────────────────────
EXIF_DATE_TAGS = (
    36867,  # DateTimeOriginal  — when the shutter fired
    36868,  # DateTimeDigitized — when the image was scanned/digitised
    306,    # DateTime          — file modification time recorded by camera
)

EXIF_MODEL_TAG = 272  # Camera model name
