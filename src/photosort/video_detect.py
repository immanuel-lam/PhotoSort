"""
Device detection for video files.

Two strategies:
  1. Container metadata — reads Make/Model atoms from MP4/MOV files (hachoir3).
  2. Proximity matching — finds the nearest photo (by timestamp) that was
     assigned to a known device, and inherits that device label.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from photosort.constants import VIDEO_EXTENSIONS
from photosort.device_detect import _sanitize

# ── Optional: hachoir3 for container metadata ─────────────────────────────────
try:
    from hachoir.parser import createParser       # type: ignore
    from hachoir.metadata import extractMetadata  # type: ignore
    import hachoir.core.config as hachoir_config  # type: ignore
    hachoir_config.quiet = True                   # suppress hachoir stderr noise
    HACHOIR_AVAILABLE = True
except ImportError:
    HACHOIR_AVAILABLE = False


# ── Container metadata ────────────────────────────────────────────────────────

def get_video_device_from_metadata(filepath: Path) -> Optional[str]:
    """
    Try to extract a camera model name from MP4/MOV container metadata.
    Returns a sanitized device name or None.
    """
    if not HACHOIR_AVAILABLE:
        return None
    if filepath.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    try:
        with createParser(str(filepath)) as parser:
            if not parser:
                return None
            metadata = extractMetadata(parser)
            if not metadata:
                return None
            # hachoir exposes metadata fields via .get(key) or iteration
            # 'producer' often contains device/encoder info on Android/iPhone
            for key in ("producer", "device", "model", "make"):
                try:
                    val = metadata.get(key)
                    if val:
                        text = str(val).strip()
                        if text and len(text) > 1:
                            sanitized = _sanitize(text)
                            if sanitized and sanitized != "unknown-device":
                                return sanitized
                except Exception:
                    continue
    except Exception:
        pass
    return None


# ── Device timeline (built from processed photos) ────────────────────────────

class DeviceTimeline:
    """
    Sorted list of (datetime, device_name) from already-processed photos.
    Used to find the nearest device for a video by timestamp proximity.
    """

    def __init__(self):
        self._times:   list[datetime] = []
        self._devices: list[str]      = []

    def add(self, dt: datetime, device: str) -> None:
        """Insert a (dt, device) entry, keeping the list sorted by dt."""
        idx = bisect.bisect_left(self._times, dt)
        self._times.insert(idx, dt)
        self._devices.insert(idx, device)

    def nearest(
        self,
        dt: datetime,
        window_minutes: int,
    ) -> tuple[Optional[str], Optional[timedelta]]:
        """
        Return (device, delta) for the closest entry within *window_minutes*.
        delta is the absolute time difference.
        Returns (None, None) if the timeline is empty or no entry is within window.
        """
        if not self._times:
            return None, None

        window = timedelta(minutes=window_minutes)
        idx = bisect.bisect_left(self._times, dt)

        best_device: Optional[str]      = None
        best_delta:  Optional[timedelta] = None

        for i in (idx - 1, idx):
            if 0 <= i < len(self._times):
                delta = abs(dt - self._times[i])
                if delta <= window:
                    if best_delta is None or delta < best_delta:
                        best_delta  = delta
                        best_device = self._devices[i]

        return best_device, best_delta


# ── Confidence threshold ──────────────────────────────────────────────────────

# Matches within this many minutes are considered "confident".
# Matches beyond this (but still within the user window) are flagged as warnings.
CONFIDENT_MINUTES = 10


def is_confident_match(delta: timedelta) -> bool:
    return delta.total_seconds() <= CONFIDENT_MINUTES * 60
