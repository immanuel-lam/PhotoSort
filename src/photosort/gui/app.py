"""Main PhotoSort GUI window."""

from __future__ import annotations

import sys
import time
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

# Appearance mode cycling order and labels
_APPEARANCE_MODES = ["System", "Light", "Dark"]
_APPEARANCE_LABELS = {"System": "System", "Light": "Light", "Dark": "Dark"}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PhotoSort")
        self.resizable(True, True)
        self.minsize(700, 740)

        self._worker: Optional[SortWorker] = None
        self._total_media = 0

        # Timing state
        self._sort_start_time: Optional[float] = None
        self._sort_active = False

        # Error tracking for badge
        self._error_count = 0

        # Appearance cycling
        self._appearance_idx = 0   # index into _APPEARANCE_MODES

        # Scan-phase flag (for indeterminate → determinate progress bar switch)
        self._scan_phase = False

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = ctk.CTkScrollableFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=20)
        outer.columnconfigure(0, weight=1)

        row = 0

        # ── Title row: app name + dark/light toggle ───────────────────────────
        title_row = ctk.CTkFrame(outer, fg_color="transparent")
        title_row.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        title_row.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            title_row, text="PhotoSort",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self._appearance_btn = ctk.CTkButton(
            title_row,
            text="System Mode",
            width=110,
            height=28,
            fg_color=("gray78", "gray28"),
            hover_color=("gray68", "gray38"),
            text_color=("gray15", "gray90"),
            font=ctk.CTkFont(size=12),
            command=self._cycle_appearance,
        )
        self._appearance_btn.grid(row=0, column=1, sticky="e")
        row += 1

        # ── Input folder ──────────────────────────────────────────────────────
        self._input_picker = FolderPicker(
            outer, label="Input Folder", on_change=self._on_input_change_and_cmd
        )
        self._input_picker.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        row += 1

        # ── Output folder ─────────────────────────────────────────────────────
        self._output_picker = FolderPicker(
            outer, label="Output Folder", on_change=lambda _: self._update_cli_cmd()
        )
        self._output_picker.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        row += 1

        # ── Options card ──────────────────────────────────────────────────────
        options_card = ctk.CTkFrame(outer)
        options_card.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        options_card.columnconfigure(0, weight=1)
        row += 1

        ctk.CTkLabel(
            options_card, text="Options",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 8))

        self._date_fmt_selector = DateFormatSelector(
            options_card, on_change=self._update_cli_cmd
        )
        self._date_fmt_selector.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        self._priority_selector = PrioritySelector(
            options_card, on_change=self._update_cli_cmd
        )
        self._priority_selector.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))

        self._proximity_selector = ProximityWindowSelector(
            options_card, on_change=self._update_cli_cmd
        )
        self._proximity_selector.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 8))

        # Workers row
        workers_row = ctk.CTkFrame(options_card, fg_color="transparent")
        workers_row.grid(row=4, column=0, sticky="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(workers_row, text="Parallel workers:").pack(side="left", padx=(0, 8))
        self._workers_var = ctk.StringVar(value="1")
        self._workers_var.trace_add("write", lambda *_: self._update_cli_cmd())
        ctk.CTkEntry(workers_row, textvariable=self._workers_var, width=48).pack(side="left")
        ctk.CTkLabel(
            workers_row, text="  (1 = safe for HDDs,  4+ for SSDs / network shares)",
            text_color="gray",
        ).pack(side="left", padx=(6, 0))

        dry_row = ctk.CTkFrame(options_card, fg_color="transparent")
        dry_row.grid(row=5, column=0, sticky="w", padx=16, pady=(0, 12))
        self._dry_run_var = ctk.BooleanVar(value=True)
        self._dry_run_var.trace_add("write", lambda *_: self._update_cli_cmd())
        ctk.CTkCheckBox(
            dry_row, text="Dry Run (preview only — no files will be moved)",
            variable=self._dry_run_var,
        ).pack(side="left")

        # ── CLI command bar ───────────────────────────────────────────────────
        cli_card = ctk.CTkFrame(outer)
        cli_card.grid(row=row, column=0, sticky="ew", pady=(0, 16))
        cli_card.columnconfigure(1, weight=1)
        row += 1

        ctk.CTkLabel(
            cli_card, text="CLI equivalent",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="gray",
        ).grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(8, 8))

        self._cli_cmd_var = ctk.StringVar(value="python main.py <input> <output>")
        cli_entry = ctk.CTkEntry(
            cli_card, textvariable=self._cli_cmd_var,
            font=ctk.CTkFont(family=_MONO_FONT, size=11),
            state="readonly",
            fg_color=("gray90", "gray20"),
        )
        cli_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 8))

        ctk.CTkButton(
            cli_card, text="Copy", width=56, height=28,
            font=ctk.CTkFont(size=11),
            fg_color=("gray78", "gray30"),
            hover_color=("gray65", "gray42"),
            text_color=("gray15", "gray90"),
            command=self._copy_cli_cmd,
        ).grid(row=0, column=2, sticky="e", padx=(0, 12), pady=(8, 8))

        # ── Buttons row: Sort + Pause/Resume ─────────────────────────────────
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

        # ── Progress label ────────────────────────────────────────────────────
        self._progress_label = ctk.CTkLabel(outer, text="", font=ctk.CTkFont(size=12))
        self._progress_label.grid(row=row, column=0, sticky="w", pady=(0, 2))
        row += 1

        # ── Progress bar (switches between indeterminate ↔ determinate) ───────
        self._progress_bar = ctk.CTkProgressBar(outer, height=16)
        self._progress_bar.set(0)
        self._progress_bar.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        row += 1

        # ── Timing / ETA row ──────────────────────────────────────────────────
        timing_row = ctk.CTkFrame(outer, fg_color="transparent")
        timing_row.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        timing_row.columnconfigure(0, weight=1)
        row += 1

        self._elapsed_label = ctk.CTkLabel(
            timing_row, text="",
            font=ctk.CTkFont(size=11), text_color="gray",
        )
        self._elapsed_label.grid(row=0, column=0, sticky="w")

        self._eta_label = ctk.CTkLabel(
            timing_row, text="",
            font=ctk.CTkFont(size=11), text_color="gray",
        )
        self._eta_label.grid(row=0, column=1, sticky="e")

        # ── Log header: title + Clear button ─────────────────────────────────
        log_header = ctk.CTkFrame(outer, fg_color="transparent")
        log_header.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        log_header.columnconfigure(0, weight=1)
        row += 1

        ctk.CTkLabel(
            log_header, text="Log",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            log_header, text="Clear Log", width=80, height=26,
            fg_color=("gray78", "gray28"),
            hover_color=("gray65", "gray40"),
            text_color=("gray15", "gray85"),
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        ).grid(row=0, column=1, sticky="e")

        self._log_box = ctk.CTkTextbox(
            outer, height=220, font=ctk.CTkFont(family=_MONO_FONT, size=12)
        )
        self._log_box.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        self._log_box.configure(state="disabled")
        row += 1

        self._summary_label = ctk.CTkLabel(outer, text="", font=ctk.CTkFont(size=12))
        self._summary_label.grid(row=row, column=0, sticky="w", pady=(0, 16))
        row += 1

        # ── Errors panel (hidden until needed) ────────────────────────────────
        self._error_header = ctk.CTkLabel(
            outer, text="Errors",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#cc2200", "#ff5555"),
        )
        self._error_box = ctk.CTkTextbox(
            outer, height=100,
            font=ctk.CTkFont(family=_MONO_FONT, size=12),
            text_color=("#cc2200", "#ff5555"),
        )
        self._error_box.configure(state="disabled")
        self._error_row_header = row
        self._error_row_box    = row + 1
        self._outer = outer

    # ── CLI command display ───────────────────────────────────────────────────

    def _update_cli_cmd(self):
        """Rebuild the CLI command string from current widget state."""
        from photosort.models import DEFAULT_DATE_FORMAT, DEFAULT_PRIORITY
        src = self._input_picker.get()
        dst = self._output_picker.get()
        src_str = str(src) if src else "<input>"
        dst_str = str(dst) if dst else "<output>"

        parts = ["python main.py", src_str, dst_str]

        if self._dry_run_var.get():
            parts.append("--dry-run")

        fmt = self._date_fmt_selector.get()
        if fmt != DEFAULT_DATE_FORMAT:
            parts.append(f'--format "{fmt}"')

        priority = self._priority_selector.get()
        default_priority = DEFAULT_PRIORITY
        if priority != default_priority:
            parts.append("--priority " + ",".join(s.value for s in priority))

        prox = self._proximity_selector.get()
        if prox != 30:
            parts.append(f"--proximity-window {prox}")

        try:
            workers = max(1, int(self._workers_var.get()))
        except ValueError:
            workers = 1
        if workers != 1:
            parts.append(f"--workers {workers}")

        self._cli_cmd_var.set(" ".join(parts))

    def _copy_cli_cmd(self):
        self.clipboard_clear()
        self.clipboard_append(self._cli_cmd_var.get())

    # ── Appearance toggle ─────────────────────────────────────────────────────

    def _cycle_appearance(self):
        """Cycle through System → Light → Dark → System …"""
        self._appearance_idx = (self._appearance_idx + 1) % len(_APPEARANCE_MODES)
        mode = _APPEARANCE_MODES[self._appearance_idx]
        ctk.set_appearance_mode(mode)
        self._appearance_btn.configure(text=f"{mode} Mode")

    # ── Elapsed timer ─────────────────────────────────────────────────────────

    def _tick_elapsed(self):
        """Called every second to refresh the elapsed-time label."""
        if not self._sort_active:
            return
        if self._sort_start_time is not None:
            elapsed = time.monotonic() - self._sort_start_time
            self._elapsed_label.configure(text=f"Elapsed: {self._fmt_duration(elapsed)}")
        self.after(1000, self._tick_elapsed)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m {s:02d}s"

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_input_change_and_cmd(self, path: Path):
        self._update_cli_cmd()
        self._on_input_change(path)

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

        # Indeterminate progress bar during the scan phase
        self._scan_phase = True
        self._progress_bar.configure(mode="indeterminate")
        self._progress_bar.start()
        self._progress_label.configure(text="Scanning for media files…")

        self._clear_log()
        self._hide_errors()
        self._summary_label.configure(text="")
        self._error_count = 0
        self._elapsed_label.configure(text="")
        self._eta_label.configure(text="")

        # Start elapsed-time ticker
        self._sort_start_time = time.monotonic()
        self._sort_active = True
        self._tick_elapsed()

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
        # First real progress update: switch from indeterminate → determinate
        if self._scan_phase:
            self._scan_phase = False
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar.set(0)

        if total > 0:
            self._progress_bar.set(current / total)

        remaining = total - current
        self._progress_label.configure(text=f"{current:,} / {total:,} files processed")

        # Live ETA
        if self._sort_start_time is not None and current > 0:
            elapsed = time.monotonic() - self._sort_start_time
            rate = current / elapsed  # files per second
            if rate > 0 and remaining > 0:
                eta_sec = remaining / rate
                self._eta_label.configure(
                    text=f"{remaining:,} remaining  ·  ETA {self._fmt_duration(eta_sec)}"
                )
            else:
                self._eta_label.configure(text="")

        if record.error:
            self._error_count += 1
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
            hash_str = f"  [{record.sha256[:8]}...]" if record.sha256 else ""
            self._append_log(f"[{current}/{total}] {tag}  {record.source_path.name} → {dest_str}{hash_str}\n")

    def _finish(self, result: SortResult):
        # Stop the live timer and show final elapsed time
        self._sort_active = False
        elapsed = time.monotonic() - self._sort_start_time if self._sort_start_time else 0
        self._elapsed_label.configure(text=f"Completed in {self._fmt_duration(elapsed)}")
        self._eta_label.configure(text="")

        self._progress_bar.set(1)
        action = "Would move" if result.moved == 0 and result.total_files > 0 else "Moved"

        expected = result.total_files
        actual   = result.moved + result.duplicates
        check    = "[OK] 1:1" if actual == expected else f"[!] {actual}/{expected}"

        warn_str = f"  |  [!] {result.proximity_warnings} prox. warning(s)" if result.proximity_warnings else ""
        err_badge = f"Errors: {result.errors} (!)" if result.errors else "Errors: 0"
        self._summary_label.configure(
            text=(
                f"{action}: {result.moved}  |  Duplicates: {result.duplicates}  |  "
                f"{err_badge}  |  {check}{warn_str}"
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
                self._append_log(f"\n{note}Unmatched video report -> {unmatched}\n")

            dup_rep = generate_duplicate_report(out, dry_run=dry)
            if dup_rep:
                self._append_log(f"{note}Duplicate report       -> {dup_rep}\n")

            misc_rep = generate_misc_report(out, dry_run=dry)
            if misc_rep:
                self._append_log(f"{note}Misc report            -> {misc_rep}\n")

    def _reset_buttons(self):
        self._sort_active = False  # stop timer if still running (e.g. error path)
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
        badge = f" ({self._error_count})" if self._error_count > 0 else ""
        self._error_header.configure(text=f"Errors{badge}")
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
