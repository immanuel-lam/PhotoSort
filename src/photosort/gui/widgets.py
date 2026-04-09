"""Reusable compound widgets for the PhotoSort GUI."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from photosort.models import DATE_FORMAT_PRESETS, DEFAULT_PROXIMITY_WINDOW, DateSource


# ── Folder picker ─────────────────────────────────────────────────────────────

class FolderPicker(ctk.CTkFrame):
    """Label + entry + Browse button + optional status line."""

    def __init__(
        self,
        master,
        label: str,
        on_change: Optional[Callable[[Path], None]] = None,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_change = on_change

        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )

        self._entry = ctk.CTkEntry(self, placeholder_text="Select a folder…", width=400)
        self._entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(self, text="Browse", width=80, command=self._browse).grid(
            row=1, column=1
        )

        self._status = ctk.CTkLabel(self, text="", text_color="gray", font=ctk.CTkFont(size=12))
        self._status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.columnconfigure(0, weight=1)

    def _browse(self):
        path = filedialog.askdirectory()
        if path:
            self.set(Path(path))

    def set(self, path: Path):
        self._entry.delete(0, "end")
        self._entry.insert(0, str(path))
        if self._on_change:
            self._on_change(path)

    def get(self) -> Optional[Path]:
        text = self._entry.get().strip()
        return Path(text) if text else None

    def set_status(self, text: str, color: str = "gray"):
        self._status.configure(text=text, text_color=color)


# ── Date format selector ──────────────────────────────────────────────────────

_CUSTOM_LABEL = "Custom…"

class DateFormatSelector(ctk.CTkFrame):
    """Dropdown of date format presets with an optional custom entry."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        ctk.CTkLabel(self, text="Date Format:", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )

        self._labels   = [label for _, label in DATE_FORMAT_PRESETS] + [_CUSTOM_LABEL]
        self._fmt_map  = {label: fmt for fmt, label in DATE_FORMAT_PRESETS}

        self._combo = ctk.CTkComboBox(
            self,
            values=self._labels,
            width=360,
            command=self._on_select,
        )
        self._combo.set(DATE_FORMAT_PRESETS[0][1])
        self._combo.grid(row=0, column=1, sticky="w")

        self._custom_entry = ctk.CTkEntry(self, placeholder_text="e.g. %Y/%m/%d", width=220)
        self._custom_entry.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self._custom_entry.grid_remove()

    def _on_select(self, choice: str):
        if choice == _CUSTOM_LABEL:
            self._custom_entry.grid()
        else:
            self._custom_entry.grid_remove()

    def get(self) -> str:
        choice = self._combo.get()
        if choice == _CUSTOM_LABEL:
            return self._custom_entry.get().strip() or DATE_FORMAT_PRESETS[0][0]
        return self._fmt_map.get(choice, DATE_FORMAT_PRESETS[0][0])


# ── Priority selector ─────────────────────────────────────────────────────────

class PrioritySelector(ctk.CTkFrame):
    """Four source buttons with ↑↓ arrows to reorder date extraction priority."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        ctk.CTkLabel(self, text="Sort Priority:", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )

        self._order: list[DateSource] = [
            DateSource.EXIF,
            DateSource.FILENAME,
            DateSource.CREATED,
            DateSource.MODIFIED,
        ]
        self._slot_frames: list[ctk.CTkFrame] = []
        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._container.grid(row=0, column=1, sticky="w")
        self._render()

    def _render(self):
        for w in self._slot_frames:
            w.destroy()
        self._slot_frames.clear()

        for i, source in enumerate(self._order):
            frame = ctk.CTkFrame(self._container, fg_color="transparent")
            frame.grid(row=0, column=i, padx=4)

            ctk.CTkLabel(frame, text=f"{i+1}.", width=18, font=ctk.CTkFont(size=11),
                         text_color="gray").pack(side="top")

            btn = ctk.CTkButton(
                frame, text=source.label(), width=90,
                fg_color=("gray75", "gray30"),
                hover_color=("gray65", "gray40"),
                text_color=("black", "white"),
            )
            btn.pack(side="top", pady=(2, 2))

            arrow_row = ctk.CTkFrame(frame, fg_color="transparent")
            arrow_row.pack(side="top")

            ctk.CTkButton(
                arrow_row, text="←", width=40, height=22,
                fg_color=("gray80", "gray25"),
                hover_color=("gray65", "gray40"),
                command=lambda idx=i: self._move(idx, -1),
            ).pack(side="left", padx=1)

            ctk.CTkButton(
                arrow_row, text="→", width=40, height=22,
                fg_color=("gray80", "gray25"),
                hover_color=("gray65", "gray40"),
                command=lambda idx=i: self._move(idx, +1),
            ).pack(side="left", padx=1)

            self._slot_frames.append(frame)

    def _move(self, idx: int, direction: int):
        target = idx + direction
        if 0 <= target < len(self._order):
            self._order[idx], self._order[target] = self._order[target], self._order[idx]
            self._render()

    def get(self) -> list[DateSource]:
        return list(self._order)


# ── Proximity window selector ─────────────────────────────────────────────────

class ProximityWindowSelector(ctk.CTkFrame):
    """
    Slider + label for the video proximity-matching window.
    Shows 0 (disabled) through 120 minutes.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        ctk.CTkLabel(
            self, text="Video Proximity:", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))

        self._var = ctk.IntVar(value=DEFAULT_PROXIMITY_WINDOW)

        self._slider = ctk.CTkSlider(
            self, from_=0, to=120, number_of_steps=24,
            variable=self._var, width=200,
            command=self._on_change,
        )
        self._slider.grid(row=0, column=1, sticky="w")

        self._label = ctk.CTkLabel(self, text=self._format(DEFAULT_PROXIMITY_WINDOW),
                                   width=120, anchor="w")
        self._label.grid(row=0, column=2, sticky="w", padx=(10, 0))

        ctk.CTkLabel(
            self,
            text="Match videos to the nearest photo's device. Gaps >10 min shown as warnings.",
            font=ctk.CTkFont(size=11), text_color="gray",
        ).grid(row=1, column=1, columnspan=2, sticky="w", pady=(2, 0))

    def _on_change(self, value):
        minutes = int(value)
        self._label.configure(text=self._format(minutes))

    @staticmethod
    def _format(minutes: int) -> str:
        if minutes == 0:
            return "Disabled"
        return f"{minutes} min window"

    def get(self) -> int:
        return int(self._var.get())
