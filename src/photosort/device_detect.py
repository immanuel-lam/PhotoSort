"""EXIF camera model extraction for device-based folder sorting."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from photosort.constants import EXIF_MODEL_TAG, PHOTO_EXTENSIONS

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# Characters illegal in folder names on Windows (and just bad practice everywhere)
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE   = re.compile(r'\s+')


def _sanitize(name: str) -> str:
    """Make a camera model string safe to use as a folder name."""
    name = _ILLEGAL_CHARS.sub('-', name)
    name = _MULTI_SPACE.sub(' ', name)
    name = name.strip(' .-')
    return name or "unknown-device"


def get_device_name(filepath: Path) -> Optional[str]:
    """
    Return the sanitized camera model from EXIF tag 272, or None.
    None means the engine will place the file under 'misc/'.
    """
    if not PILLOW_AVAILABLE:
        return None
    if filepath.suffix.lower() not in PHOTO_EXTENSIONS:
        return None
    try:
        with Image.open(filepath) as img:
            exif = img.getexif()
            if not exif:
                return None
            model = exif.get(EXIF_MODEL_TAG)
            if not model or not str(model).strip():
                return None
            return _sanitize(str(model))
    except Exception:
        return None
