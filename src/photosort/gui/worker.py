"""Background thread wrapper for the sorting engine."""

from __future__ import annotations

import threading
from typing import Callable, Optional

from photosort.engine import sort_files
from photosort.models import FileRecord, SortConfig, SortResult


class SortWorker:
    """Runs sort_files in a daemon thread and posts progress/completion callbacks."""

    def __init__(
        self,
        config: SortConfig,
        on_progress: Callable[[int, int, FileRecord], None],
        on_complete: Callable[[SortResult], None],
        on_error: Optional[Callable[[Exception], None]] = None,
        on_scan_progress: Optional[Callable[[int], None]] = None,
    ):
        self._config           = config
        self._on_progress      = on_progress
        self._on_complete      = on_complete
        self._on_error         = on_error
        self._on_scan_progress = on_scan_progress
        self._thread: Optional[threading.Thread] = None

        # Pause event: set = running, cleared = paused
        self._pause_event = threading.Event()
        self._pause_event.set()

    # ── Public controls ───────────────────────────────────────────────────────

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def paused(self) -> bool:
        return not self._pause_event.is_set()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self):
        try:
            result = sort_files(
                self._config,
                on_progress=self._on_progress,
                on_scan_progress=self._on_scan_progress,
                pause_event=self._pause_event,
            )
            self._on_complete(result)
        except Exception as exc:
            if self._on_error:
                self._on_error(exc)
