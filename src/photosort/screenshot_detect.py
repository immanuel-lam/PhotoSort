"""
Screenshot and screen recording detection by filename pattern.

Files matching these patterns are routed to screenshots/ instead of misc/.
"""

from __future__ import annotations

import re
from pathlib import Path

# ── Detection patterns ────────────────────────────────────────────────────────

_SCREENSHOT_PATTERNS = [
    # Android (Samsung, Google Pixel, MIUI, stock)
    re.compile(r'^Screenshot[_\s-]', re.IGNORECASE),
    # macOS / iOS  "Screenshot 2024-07-15 at 08.30.12.png"
    re.compile(r'^Screenshot\s+\d{4}', re.IGNORECASE),
    # Windows Snipping Tool / Win+PrtSc  "Screenshot (1).png"
    re.compile(r'^Screenshot\s*\(\d+\)', re.IGNORECASE),
]

_SCREEN_RECORDING_PATTERNS = [
    # Samsung  "Screen_recording_20240715-083012.mp4"
    re.compile(r'^Screen[_\s]recording[_\s-]', re.IGNORECASE),
    # macOS / iOS  "Screen Recording 2024-07-15 at 08.30.12.mov"
    re.compile(r'^Screen\s+Recording\s+\d{4}', re.IGNORECASE),
    # Android generic  "screenrecord_*.mp4"
    re.compile(r'^screenrecord[_\s-]', re.IGNORECASE),
    # Xiaomi / MIUI  "ScreenRecorder_*.mp4"
    re.compile(r'^ScreenRecorder[_\s-]', re.IGNORECASE),
    # Some Android ROMs  "record_screen_*.mp4"
    re.compile(r'^record[_\s]screen[_\s-]', re.IGNORECASE),
]


def is_screenshot(filepath: Path) -> bool:
    """Return True if the file looks like a screenshot."""
    name = filepath.stem
    return any(p.match(name) for p in _SCREENSHOT_PATTERNS)


def is_screen_recording(filepath: Path) -> bool:
    """Return True if the file looks like a screen recording."""
    name = filepath.stem
    return any(p.match(name) for p in _SCREEN_RECORDING_PATTERNS)


def is_screenshot_or_recording(filepath: Path) -> bool:
    """Return True if the file should be routed to screenshots/ instead of misc/."""
    return is_screenshot(filepath) or is_screen_recording(filepath)
