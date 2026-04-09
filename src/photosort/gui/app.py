"""Main PhotoSort GUI window."""

from __future__ import annotations

import sys
from pathlib import Path
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from photosort.engine import MISC_FOLDER, scan_media_files
from photosort.gui.widgets import DateFormatSelector, FolderPicker, PrioritySelector, ProximityWindowSelector
from photosort.gui.worker import SortWorker
from photosort.models import FileRecord, SortConfig, SortResult

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# "monospace" is not a real font on Windows; use platform-appropriate fallback
_MONO_FONT = "Consolas" if sys.platform == "win32" else (
    "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"
)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PhotoSort")
        self.resizable(True, True)
        self.minsize(680, 720)

        self._worker: Optional[SortWorker] = None
        self._total_media = 0

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=20)
        outer.columnconfigure(0, weight=1)

        row = 0

        # Title
        ctk.CTkLabel(
            outer, text="PhotoSort",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=row, column=0, sticky="w", pady=(0, 16))
        row += 1

        # Input folder
        self._input_picker = FolderPicker(
            outer, label="Input Folder", on_change=self._on_input_change
        )
        self._input_picker.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        # Output folder
        self._output_picker = FolderPicker(outer, label="Output Folder")
        self._output_picker.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        row += 1

        # Options card
        options_card = ctk.CTkFrame(outer)
        options_card.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        options_card.columnconfigure(0, weight=1)
        row += 1

        ctk.CTkLabel(
            options_card, text="Options",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 8))

        self._date_fmt_selector = DateFormatSelector(options_card)
        self._date_fmt_selector.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        self._priority_selector = PrioritySelector(options_card)
        self._priority_selector.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))

        self._proximity_selector = ProximityWindowSelector(options_card)
        self._proximity_selector.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 8))

        # Workers row
        workers_row = ctk.CTkFrame(options_card, fg_color="transparent")
        workers_row.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(workers_row, text="Parallel workers:").pack(side="left", padx=(0, 8))
        self._workers_var = ctk.StringVar(value="1")
        ctk.CTkEntry(workers_row, textvariable=self._workers_var, width=48).pack(side="left")
        ctk.CTkLabel(
            workers_row, text="  (1 = safe for HDDs,  4+ for SSDs / network shares)",
            text_color="gray",
        ).pack(side="left", padx=(6, 0))

        dry_row = ctk.CTkFrame(options_card, fg_color="transparent")
        dry_row.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 12))
        self._dry_run_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            dry_row, text="Dry Run (preview only — no files will be moved)",
            variable=self._dry_run_var,
        ).pack(side="left")

        # Buttons row: Sort + Pause/Resume
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        btn_row.columnconfigure(0, weight=1)
        row += 1

        self._sort_btn = ctk.CTkButton(
            btn_row, text="Sort Files", height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_sort_click,
        )
        self._sort_btn.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self._pause_btn = ctk.CTkButton(
            btn_row, text="Pause", width=90, height=42,
            fg_color="gray40", hover_color="gray30",
            command=self._on_pause_click,
            state="disabled",
        )
        self._pause_btn.grid(row=0, column=1, sticky="e")

        # Scan + sort progress
        self._progress_label = ctk.CTkLabel(outer, text="", font=ctk.CTkFont(size=12))
        self._progress_label.grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        self._progress_bar = ctk.CTkProgressBar(outer, height=16)
        self._progress_bar.set(0)
        self._progress_bar.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        row += 1

        # Log
        ctk.CTkLabel(
            outer, text="Log",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1

        self._log_box = ctk.CTkTextbox(outer, height=220, font=ctk.CTkFont(family=_MONO_FONT, size=12))
        self._log_box.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._log_box.configure(state="disabled")
        row += 1

        self._summary_label = ctk.CTkLabel(outer, text="", font=ctk.CTkFont(size=12))
        self._summary_label.grid(row=row, column=0, sticky="w", pady=(0, 16))
        row += 1

        # Errors (hidden until needed)
        self._error_header = ctk.CTkLabel(
            outer, text="Errors",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="red",
        )
        self._error_box = ctk.CTkTextbox(
            outer, height=100,
            font=ctk.CTkFont(family=_MONO_FONT, size=12),
            text_color="red",
        )
        self._error_box.configure(state="disabled")
        self._error_row_header = row
        self._error_row_box    = row + 1
        self._outer = outer

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_input_change(self, path: Path):
        self._input_picker.set_status("Scanning…", color="gray")
        self.after(50, lambda: self._scan_input(path))

    def _scan_input(self, path: Path):
        try:
            all_files   = [f for f in path.rglob("*") if f.is_file()]
            media_files = scan_media_files(path)
            self._total_media = len(media_files)
            self._input_picker.set_status(
                f"{len(all_files):,} total files  |  {self._total_media:,} media files",
                color=("gray20", "gray80"),
            )
        except Exception as exc:
            self._input_picker.set_status(f"Error: {exc}", color="red")

    def _on_sort_click(self):
        src = self._input_picker.get()
        dst = self._output_picker.get()

        if not src or not src.is_dir():
            messagebox.showerror("PhotoSort", "Please select a valid input folder.")
            return
        if not dst:
            messagebox.showerror("PhotoSort", "Please select an output folder.")
            return
        if src == dst:
            messagebox.showerror("PhotoSort", "Input and output folders must be different.")
            return

        try:
            workers = max(1, int(self._workers_var.get()))
        except ValueError:
            workers = 1

        dry_run = self._dry_run_var.get()

        if not dry_run:
            if not messagebox.askyesno(
                "PhotoSort — Live Run",
                f"This will move {self._total_media:,} media file(s).\n\n"
                "Files will be moved from the input folder.\n"
                "This cannot be undone. Continue?",
                icon="warning",
            ):
                return

        config = SortConfig(
            source=src,
            destination=dst,
            dry_run=dry_run,
            date_format=self._date_fmt_selector.get(),
            priority=self._priority_selector.get(),
            proximity_window_minutes=self._proximity_selector.get(),
            workers=workers,
        )

        self._start_sort(config)

    def _on_pause_click(self):
        if self._worker is None:
            return
        if self._worker.paused:
            self._worker.resume()
            self._pause_btn.configure(
                text="Pause",
                fg_color="gray40",
                hover_color="gray30",
            )
        else:
            self._worker.pause()
            self._pause_btn.configure(
                text="Resume",
                fg_color=("#e07b00", "#c06800"),
                hover_color=("#c06800", "#a05500"),
            )

    def _start_sort(self, config: SortConfig):
        self._sort_btn.configure(state="disabled", text="Sorting…")
        self._pause_btn.configure(state="normal", text="Pause")
        self._progress_bar.set(0)
        self._progress_label.configure(text="Scanning for media files…")
        self._clear_log()
        self._hide_errors()
        self._summary_label.configure(text="")

        self._worker = SortWorker(
            config=config,
            on_progress=self._on_progress,
            on_complete=self._on_complete,
            on_error=self._on_worker_error,
            on_scan_progress=self._on_scan_progress,
        )
        self._worker.start()

    # ── Worker callbacks (called from background thread) ──────────────────────

    def _on_scan_progress(self, count: int):
        self.after(0, self._update_scan_label, count)

    def _on_progress(self, current: int, total: int, record: FileRecord):
        self.after(0, self._update_progress, current, total, record)

    def _on_complete(self, result: SortResult):
        self.after(0, self._finish, result)

    def _on_worker_error(self, exc: Exception):
        self.after(0, lambda: messagebox.showerror("PhotoSort — Error", str(exc)))
        self.after(0, self._reset_buttons)

    # ── UI updates (always on main thread via .after) ─────────────────────────

    def _update_scan_label(self, count: int):
        self._progress_label.configure(text=f"Scanning… {count:,} files found")

    def _update_progress(self, current: int, total: int, record: FileRecord):
        if total > 0:
            self._progress_bar.set(current / total)
        self._progress_label.configure(text=f"{current:,} / {total:,} files processed")

        if record.error:
            self._append_log(f"ERROR  {record.source_path.name}: {record.error}\n", color="red")
            self._append_error(f"{record.source_path.name}: {record.error}\n")
        elif record.proximity_warning:
            mins = int(record.proximity_delta.total_seconds() // 60) if record.proximity_delta else "?"
            self._append_log(
                f"[{current}/{total}] PROX? {mins}min  {record.source_path.name}"
                f"  →  {record.device}  (possible false match)\n",
                color="orange",
            )
        elif record.proximity_match:
            mins = int(record.proximity_delta.total_seconds() // 60) if record.proximity_delta else "?"
            dest_str = str(record.dest_path) if record.dest_path else "?"
            self._append_log(
                f"[{current}/{total}] PROX  {mins}min  {record.source_path.name}  →  {dest_str}\n"
            )
        else:
            tag = "DUP  " if record.is_duplicate else (
                record.date_source.value.upper().ljust(8) if record.date_source else "?".ljust(8)
            )
            dest_str = str(record.dest_path) if record.dest_path else "?"
            hash_str = f"  [{record.sha256[:8]}…]" if record.sha256 else ""
            self._append_log(f"[{current}/{total}] {tag}  {record.source_path.name} → {dest_str}{hash_str}\n")

    def _finish(self, result: SortResult):
        self._progress_bar.set(1)
        action = "Would move" if result.moved == 0 and result.total_files > 0 else "Moved"

        expected = result.total_files
        actual   = result.moved + result.duplicates
        check    = "[OK] 1:1" if actual == expected else f"[!] {actual}/{expected}"

        warn_str = f"  |  [!] {result.proximity_warnings} prox. warning(s)" if result.proximity_warnings else ""
        self._summary_label.configure(
            text=(
                f"{action}: {result.moved}  |  Duplicates: {result.duplicates}  |  "
                f"Errors: {result.errors}  |  {check}{warn_str}"
            )
        )

        if result.errors > 0:
            self._show_errors()

        self._reset_buttons()

        out = self._output_picker.get()
        if out and out.exists():
            out_count = sum(1 for f in out.rglob("*") if f.is_file())
            self._output_picker.set_status(
                f"{out_count:,} files in output folder",
                color=("gray20", "gray80"),
            )

        if out:
            from photosort.report import (
                generate_unmatched_report,
                generate_misc_report,
                generate_duplicate_report,
            )
            dry = self._dry_run_var.get()
            note = "[DRY RUN] " if dry else ""

            unmatched = generate_unmatched_report(out, dry_run=dry)
            if unmatched:
                self._append_log(f"\n{note}Unmatched video report → {unmatched}\n")

            dup_rep = generate_duplicate_report(out, dry_run=dry)
            if dup_rep:
                self._append_log(f"{note}Duplicate report       → {dup_rep}\n")

            misc_rep = generate_misc_report(out, dry_run=dry)
            if misc_rep:
                self._append_log(f"{note}Misc report            → {misc_rep}\n")

    def _reset_buttons(self):
        self._sort_btn.configure(state="normal", text="Sort Files")
        self._pause_btn.configure(
            state="disabled", text="Pause",
            fg_color="gray40", hover_color="gray30",
        )

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _append_log(self, text: str, color: Optional[str] = None):
        self._log_box.configure(state="normal")
        if color:
            tag = f"color_{color}"
            self._log_box.tag_config(tag, foreground=color)
            self._log_box.insert("end", text, tag)
        else:
            self._log_box.insert("end", text)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _append_error(self, text: str):
        self._error_box.configure(state="normal")
        self._error_box.insert("end", text)
        self._error_box.configure(state="disabled")

    def _show_errors(self):
        self._error_header.grid(
            row=self._error_row_header, column=0, sticky="w", pady=(0, 4), in_=self._outer
        )
        self._error_box.grid(
            row=self._error_row_box, column=0, sticky="ew", pady=(0, 16), in_=self._outer
        )

    def _hide_errors(self):
        self._error_header.grid_remove()
        self._error_box.grid_remove()
        self._error_box.configure(state="normal")
        self._error_box.delete("1.0", "end")
        self._error_box.configure(state="disabled")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
