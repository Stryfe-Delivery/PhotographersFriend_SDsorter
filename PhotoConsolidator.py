#!/usr/bin/env python3
"""Photo Consolidator

Recursively collects images into a selected folder's top level.
Filename conflicts are reviewed by the user, with the newest variant selected by default.
All non-retained content is moved to a timestamped quarantine folder outside the root.

Optional dependency for thumbnails:
    python -m pip install Pillow
"""

from __future__ import annotations

import csv
import errno
import hashlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

DEFAULT_SUFFIXES: Set[str] = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
RAW_SUFFIXES: Set[str] = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"}
APP_PREFIX = "_photo_consolidator_"

ProgressCB = Callable[[int, int, str], None]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageRecord:
    path: Path
    relative_path: Path
    name_key: str
    modified_ns: int
    size_bytes: int

    @property
    def modified_text(self) -> str:
        return datetime.fromtimestamp(self.modified_ns / 1_000_000_000).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ScanResult:
    records: List[ImageRecord]
    groups: Dict[str, List[ImageRecord]]
    selections: Dict[str, Path]
    inaccessible: int


@dataclass
class ExecResult:
    staging: Path
    quarantine: Path
    manifest: Path
    rows: List[List[str]]
    aborted: bool = False


class AbortException(Exception):
    """Raised internally when cancel_event is set; caught by Executor."""


# ---------------------------------------------------------------------------
# File-system helpers
# ---------------------------------------------------------------------------

def safe_move(src: Path, dst: Path, allow_overwrite: bool = False) -> Tuple[bool, str]:
    """Move src -> dst. Returns (atomic, method).

    Prefers os.replace for atomic same-filesystem renames.
    Falls back to shutil.move on EXDEV (cross-device).
    Raises FileExistsError if dst exists and allow_overwrite is False.
    """
    if not allow_overwrite and dst.exists():
        raise FileExistsError(f"Destination exists: {dst}")
    try:
        os.replace(str(src), str(dst))
        return True, "os.replace"
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            shutil.move(str(src), str(dst))
            return False, "shutil.move (cross-device)"
        raise


def same_filesystem(path_a: Path, path_b: Path) -> bool:
    try:
        return os.stat(path_a).st_dev == os.stat(path_b).st_dev
    except OSError:
        return True  # be permissive if we cannot tell


def open_in_file_manager(path: Path) -> None:
    """Open a folder (or file) with the OS default handler."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


# ---------------------------------------------------------------------------
# Scanner (pure, no Tk)
# ---------------------------------------------------------------------------

class Scanner:
    def scan(
        self,
        root: Path,
        suffixes: Set[str],
        progress_cb: ProgressCB,
        cancel_event: threading.Event,
    ) -> ScanResult:
        records: List[ImageRecord] = []
        inaccessible = 0
        checked = 0

        for current, dirnames, filenames in os.walk(root, followlinks=False):
            if cancel_event.is_set():
                raise AbortException("Scan cancelled.")
            dirnames[:] = [d for d in dirnames if not d.startswith(APP_PREFIX)]
            current_path = Path(current)
            for filename in filenames:
                checked += 1
                if checked % 200 == 0:
                    progress_cb(checked, 0, f"Scanning… {checked:,} entries checked")
                path = current_path / filename
                if path.suffix.lower() not in suffixes or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                    relative = path.relative_to(root)
                    records.append(
                        ImageRecord(
                            path=path,
                            relative_path=relative,
                            name_key=path.name.casefold(),
                            modified_ns=stat.st_mtime_ns,
                            size_bytes=stat.st_size,
                        )
                    )
                except OSError:
                    inaccessible += 1

        groups: Dict[str, List[ImageRecord]] = {}
        for record in records:
            groups.setdefault(record.name_key, []).append(record)

        selections: Dict[str, Path] = {}
        for key, variants in groups.items():
            variants.sort(
                key=lambda r: (r.modified_ns, r.size_bytes, str(r.relative_path).casefold()),
                reverse=True,
            )
            selections[key] = variants[0].path

        return ScanResult(records=records, groups=groups, selections=selections, inaccessible=inaccessible)


# ---------------------------------------------------------------------------
# Executor (pure, no Tk)
# ---------------------------------------------------------------------------

class Executor:
    def execute(
        self,
        root: Path,
        retained_records: List[ImageRecord],
        staging: Path,
        quarantine: Path,
        manifest: Path,
        progress_cb: ProgressCB,
        cancel_event: threading.Event,
        pause_event: threading.Event,
    ) -> ExecResult:
        rows: List[List[str]] = []
        aborted = False
        error_msg: Optional[str] = None

        def wait_if_paused_or_aborted() -> None:
            while pause_event.is_set() and not cancel_event.is_set():
                time.sleep(0.1)
            if cancel_event.is_set():
                raise AbortException("Aborted by user.")

        try:
            staging.mkdir(parents=False, exist_ok=False)
            quarantine.mkdir(parents=False, exist_ok=False)

            # ----- Phase 1: stage retained files -----
            total1 = len(retained_records)
            progress_cb(0, total1, "Phase 1/3: staging retained files")
            for i, record in enumerate(retained_records):
                wait_if_paused_or_aborted()
                destination = staging / record.path.name
                if destination.exists():
                    raise FileExistsError(f"Unexpected staging collision: {destination.name}")
                source_hash = self.sha256(record.path)
                atomic, method = safe_move(record.path, destination, allow_overwrite=False)
                if self.sha256(destination) != source_hash:
                    raise OSError(f"Verification failed after staging: {record.relative_path}")
                rows.append(["KEEP", str(record.relative_path), str(destination), source_hash])
                progress_cb(i + 1, total1, f"Staging: {record.path.name}")

            # ----- Phase 2a: quarantine remaining root content -----
            children = list(root.iterdir())
            total2 = len(children)
            progress_cb(0, total2, "Phase 2/3: quarantining remaining content")
            for i, child in enumerate(children):
                wait_if_paused_or_aborted()
                destination = quarantine / child.name
                atomic, method = safe_move(child, destination, allow_overwrite=False)
                rows.append(["QUARANTINE", str(child.relative_to(root)), str(destination), ""])
                progress_cb(i + 1, total2, f"Quarantining: {child.name}")

            # ----- Phase 2b: promote staged files into empty root -----
            staged_files = list(staging.iterdir())
            total3 = len(staged_files)
            progress_cb(0, total3, "Phase 3/3: promoting retained files")
            for i, staged_file in enumerate(staged_files):
                wait_if_paused_or_aborted()
                destination = root / staged_file.name
                atomic, method = safe_move(staged_file, destination, allow_overwrite=False)
                rows.append(["PROMOTE", str(staged_file), str(destination), self.sha256(destination)])
                progress_cb(i + 1, total3, f"Promoting: {staged_file.name}")

            staging.rmdir()

        except AbortException as exc:
            aborted = True
            error_msg = str(exc)
        except Exception as exc:
            error_msg = str(exc)

        # Manifest is always written, success or failure.
        try:
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["action", "source", "destination", "sha256"])
                writer.writerows(rows)
                if aborted:
                    writer.writerow(["ABORT", "", "", error_msg or ""])
                elif error_msg:
                    writer.writerow(["ERROR", "", "", error_msg])
        except OSError:
            pass

        if error_msg and not aborted:
            raise RuntimeError(error_msg)

        return ExecResult(staging=staging, quarantine=quarantine, manifest=manifest,
                          rows=rows, aborted=aborted)

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class PhotoConsolidatorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Photo Consolidator")
        self.geometry("1180x800")
        self.minsize(960, 660)

        # Persistent state (Tk main thread only)
        self.root_folder: Optional[Path] = None
        self.records: List[ImageRecord] = []
        self.groups: Dict[str, List[ImageRecord]] = {}
        self.selections: Dict[str, Path] = {}
        self.all_group_keys: List[str] = []
        self.visible_group_keys: List[str] = []
        self.current_key: Optional[str] = None
        self.preview_image = None

        # File-type configuration
        self.suffixes: Set[str] = set(DEFAULT_SUFFIXES)

        # Worker / threading
        self.is_busy = False
        self.operation: Optional[str] = None  # "scan" | "execute"
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()

        # Results for "Open" buttons
        self.last_root: Optional[Path] = None
        self.last_quarantine: Optional[Path] = None
        self.last_manifest: Optional[Path] = None

        self._build_ui()
        self._update_control_states()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # --- Toolbar ---
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")

        self.select_button = ttk.Button(toolbar, text="Select Folder", command=self.select_folder)
        self.select_button.pack(side="left")
        self.scan_button = ttk.Button(toolbar, text="Scan", command=self.scan)
        self.scan_button.pack(side="left", padx=(8, 0))
        self.execute_button = ttk.Button(toolbar, text="Execute Consolidation",
                                         command=self.execute, state="disabled")
        self.execute_button.pack(side="left", padx=(8, 0))
        self.pause_button = ttk.Button(toolbar, text="Pause", command=self.toggle_pause,
                                       state="disabled")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.abort_button = ttk.Button(toolbar, text="Abort", command=self.abort,
                                       state="disabled")
        self.abort_button.pack(side="left", padx=(8, 0))

        self.folder_var = tk.StringVar(value="No folder selected")
        ttk.Label(toolbar, textvariable=self.folder_var).pack(side="left", padx=14, fill="x", expand=True)

        # --- Progress + status ---
        prog_frame = ttk.Frame(self, padding=(12, 0, 12, 6))
        prog_frame.pack(fill="x")
        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate", length=200)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(prog_frame, textvariable=self.status_var, width=48, anchor="w").pack(side="left", padx=(10, 0))

        # --- Notebook ---
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self.consolidate_tab = ttk.Frame(self.notebook)
        self.filetypes_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.consolidate_tab, text="Consolidate")
        self.notebook.add(self.filetypes_tab, text="File Types")

        self._build_consolidate_tab()
        self._build_filetypes_tab()

        # --- Bottom bar: open buttons ---
        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")
        self.open_root_button = ttk.Button(bottom, text="Open Root", command=self.open_root, state="disabled")
        self.open_root_button.pack(side="left")
        self.open_quarantine_button = ttk.Button(bottom, text="Open Quarantine",
                                                 command=self.open_quarantine, state="disabled")
        self.open_quarantine_button.pack(side="left", padx=(8, 0))
        self.open_manifest_button = ttk.Button(bottom, text="Open Manifest Folder",
                                               command=self.open_manifest_folder, state="disabled")
        self.open_manifest_button.pack(side="left", padx=(8, 0))

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------- Consolidate

    def _build_consolidate_tab(self) -> None:
        tab = self.consolidate_tab

        # Filter + auto-select row
        filter_frame = ttk.Frame(tab, padding=(10, 8, 10, 4))
        filter_frame.pack(fill="x")

        ttk.Label(filter_frame, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(filter_frame, textvariable=self.filter_var, width=28)
        self.filter_entry.pack(side="left", padx=(4, 12))
        self.filter_entry.bind("<KeyRelease>", lambda _e: self.refresh_group_tree())

        self.conflicts_only_var = tk.BooleanVar(value=False)
        self.conflicts_check = ttk.Checkbutton(
            filter_frame, text="Show conflicts only",
            variable=self.conflicts_only_var,
            command=self.refresh_group_tree,
        )
        self.conflicts_check.pack(side="left")

        ttk.Separator(filter_frame, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Label(filter_frame, text="Auto-select conflicts:").pack(side="left")
        self.autoselect_buttons: List[ttk.Button] = []
        for label, rule in (("Newest", "newest"), ("Largest", "largest"), ("Shortest path", "shortest")):
            btn = ttk.Button(filter_frame, text=label,
                             command=lambda r=rule: self.apply_rule(r))
            btn.pack(side="left", padx=(4, 0))
            self.autoselect_buttons.append(btn)
        reset_btn = ttk.Button(filter_frame, text="Reset all to newest",
                               command=lambda: self.apply_rule("newest", only_conflicts=False))
        reset_btn.pack(side="left", padx=(12, 0))
        self.autoselect_buttons.append(reset_btn)

        # Summary
        self.summary_var = tk.StringVar(
            value="Select a folder, then scan. No files are changed during scanning."
        )
        ttk.Label(tab, textvariable=self.summary_var, padding=(12, 0, 12, 6)).pack(fill="x")

        # Body: group tree | variants + preview
        body = ttk.Panedwindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        left = ttk.Labelframe(body, text="Filename Groups", padding=6)
        right = ttk.Labelframe(body, text="Variants: select one to retain", padding=8)
        body.add(left, weight=1)
        body.add(right, weight=3)

        self.group_tree = ttk.Treeview(left, columns=("count", "status"), show="tree headings")
        self.group_tree.heading("#0", text="Filename")
        self.group_tree.heading("count", text="Files")
        self.group_tree.heading("status", text="Status")
        self.group_tree.column("#0", width=220)
        self.group_tree.column("count", width=55, anchor="center")
        self.group_tree.column("status", width=80, anchor="center")
        self.group_tree.pack(side="left", fill="both", expand=True)
        group_scroll = ttk.Scrollbar(left, orient="vertical", command=self.group_tree.yview)
        group_scroll.pack(side="right", fill="y")
        self.group_tree.configure(yscrollcommand=group_scroll.set)
        self.group_tree.bind("<<TreeviewSelect>>", self.on_group_selected)

        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=3)
        right.columnconfigure(1, weight=2)

        variant_frame = ttk.Frame(right)
        variant_frame.grid(row=0, column=0, sticky="nsew")
        preview_frame = ttk.Frame(right, padding=(12, 0, 0, 0))
        preview_frame.grid(row=0, column=1, sticky="nsew")

        columns = ("retain", "modified", "size", "path")
        self.variant_tree = ttk.Treeview(variant_frame, columns=columns, show="headings",
                                         selectmode="browse")
        self.variant_tree.heading("retain", text="Retain")
        self.variant_tree.heading("modified", text="Modified")
        self.variant_tree.heading("size", text="Size")
        self.variant_tree.heading("path", text="Relative path")
        self.variant_tree.column("retain", width=65, anchor="center")
        self.variant_tree.column("modified", width=145)
        self.variant_tree.column("size", width=85, anchor="e")
        self.variant_tree.column("path", width=340)
        self.variant_tree.pack(side="left", fill="both", expand=True)
        variant_scroll = ttk.Scrollbar(variant_frame, orient="vertical", command=self.variant_tree.yview)
        variant_scroll.pack(side="right", fill="y")
        self.variant_tree.configure(yscrollcommand=variant_scroll.set)
        self.variant_tree.bind("<<TreeviewSelect>>", self.on_variant_highlighted)
        self.variant_tree.bind("<Double-1>", self.retain_highlighted)

        ttk.Button(preview_frame, text="Retain Highlighted Variant",
                   command=self.retain_highlighted).pack(fill="x")
        self.preview_label = ttk.Label(preview_frame, text="Select a variant to preview.",
                                       anchor="center", justify="center")
        self.preview_label.pack(fill="both", expand=True, pady=(10, 0))

        note = (
            "Execution is destructive to the selected root layout, but not an immediate permanent "
            "deletion. Retained images are staged, all other content is moved to a sibling "
            "quarantine folder, then retained images are promoted to the root. Pause is available "
            "only during execution; Abort stops immediately and writes a partial manifest without "
            "further touching files."
        )
        ttk.Label(tab, text=note, wraplength=1120, padding=(12, 0, 12, 8)).pack(fill="x")

    # ---------------------------------------------------------- File types

    def _build_filetypes_tab(self) -> None:
        container = ttk.Frame(self.filetypes_tab, padding=12)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="File extensions to include when scanning:",
                  font=("", 10, "bold")).pack(anchor="w", pady=(0, 8))

        grid = ttk.Frame(container)
        grid.pack(fill="x")

        self.suffix_vars: Dict[str, tk.BooleanVar] = {}
        self.suffix_checks: List[ttk.Checkbutton] = []

        all_suffixes = sorted(DEFAULT_SUFFIXES | RAW_SUFFIXES)
        cols = 4
        for i, suffix in enumerate(all_suffixes):
            var = tk.BooleanVar(value=suffix in self.suffixes)
            cb = ttk.Checkbutton(grid, text=suffix, variable=var, command=self._on_suffix_change)
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=4, pady=2)
            self.suffix_vars[suffix] = var
            self.suffix_checks.append(cb)

        ttk.Separator(container, orient="horizontal").pack(fill="x", pady=12)

        custom_frame = ttk.Frame(container)
        custom_frame.pack(fill="x")
        ttk.Label(custom_frame, text="Custom extensions (comma-separated, e.g. .heic,.avif):").pack(side="left")
        self.custom_suffix_var = tk.StringVar()
        self.custom_suffix_entry = ttk.Entry(custom_frame, textvariable=self.custom_suffix_var)
        self.custom_suffix_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.custom_suffix_entry.bind("<KeyRelease>", lambda _e: self._on_suffix_change())

        btn_frame = ttk.Frame(container)
        btn_frame.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_frame, text="Select Defaults", command=self._select_default_suffixes).pack(side="left")
        ttk.Button(btn_frame, text="Select All", command=self._select_all_suffixes).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Select None", command=self._select_no_suffixes).pack(side="left")

        note = (
            "Note: Raw formats (CR2, NEF, ARW, DNG, ORF, etc.) are consolidated like any other image "
            "but will not display thumbnails in the preview pane. Thumbnail support requires Pillow "
            "with the appropriate codec installed. This limitation is intentional — no raw decoder is "
            "bundled.\n\n"
            "Changing file types clears the current scan. Click Scan again to apply the new selection."
        )
        ttk.Label(container, text=note, wraplength=860, justify="left",
                  foreground="#555").pack(anchor="w", pady=(16, 0))

    # ---------------------------------------------------------- File types logic

    def _collect_suffixes(self) -> Set[str]:
        suffixes = {s for s, v in self.suffix_vars.items() if v.get()}
        raw = self.custom_suffix_var.get()
        for token in raw.replace(";", ",").split(","):
            token = token.strip().lower()
            if not token:
                continue
            if not token.startswith("."):
                token = "." + token
            suffixes.add(token)
        return suffixes

    def _on_suffix_change(self) -> None:
        if self.is_busy:
            return
        new_suffixes = self._collect_suffixes()
        if new_suffixes != self.suffixes:
            self.suffixes = new_suffixes
            self.clear_scan()
            self.summary_var.set("File types changed. Click Scan to rescan.")

    def _select_default_suffixes(self) -> None:
        for s, v in self.suffix_vars.items():
            v.set(s in DEFAULT_SUFFIXES)
        self.custom_suffix_var.set("")
        self._on_suffix_change()

    def _select_all_suffixes(self) -> None:
        for v in self.suffix_vars.values():
            v.set(True)
        self._on_suffix_change()

    def _select_no_suffixes(self) -> None:
        for v in self.suffix_vars.values():
            v.set(False)
        self.custom_suffix_var.set("")
        self._on_suffix_change()

    # ---------------------------------------------------------- State mgmt

    def _set_busy(self, busy: bool, operation: Optional[str] = None) -> None:
        self.is_busy = busy
        self.operation = operation if busy else None
        if not busy:
            self.pause_event.clear()
            self.pause_button.configure(text="Pause")
        self._update_control_states()

    def _update_control_states(self) -> None:
        busy = self.is_busy
        idle_state = "disabled" if busy else "normal"

        self.select_button.configure(state=idle_state)
        self.scan_button.configure(state=idle_state)
        self.execute_button.configure(
            state="normal" if (not busy and bool(self.records)) else "disabled"
        )
        self.filter_entry.configure(state=idle_state)
        self.conflicts_check.configure(state=idle_state)
        for btn in self.autoselect_buttons:
            btn.configure(state=idle_state)
        for cb in self.suffix_checks:
            cb.configure(state=idle_state)
        self.custom_suffix_entry.configure(state=idle_state)

        # Pause only during execute
        if busy and self.operation == "execute":
            self.pause_button.configure(state="normal")
        else:
            self.pause_button.configure(state="disabled")

        self.abort_button.configure(state="normal" if busy else "disabled")

        # Open buttons
        can_open = not busy
        self.open_root_button.configure(
            state="normal" if (can_open and self.last_root) else "disabled")
        self.open_quarantine_button.configure(
            state="normal" if (can_open and self.last_quarantine) else "disabled")
        self.open_manifest_button.configure(
            state="normal" if (can_open and self.last_manifest) else "disabled")

    def clear_scan(self) -> None:
        self.records.clear()
        self.groups.clear()
        self.selections.clear()
        self.all_group_keys.clear()
        self.visible_group_keys.clear()
        self.current_key = None
        self.group_tree.delete(*self.group_tree.get_children())
        self.variant_tree.delete(*self.variant_tree.get_children())
        self.preview_label.configure(image="", text="Select a variant to preview.")
        self._reset_progress()
        self._update_control_states()
        if self.root_folder is not None:
            self.summary_var.set("Folder selected. Click Scan. No files are changed during scanning.")
        else:
            self.summary_var.set("Select a folder, then scan. No files are changed during scanning.")

    # ---------------------------------------------------------- Progress

    def _reset_progress(self) -> None:
        try:
            self.progress_bar.stop()
        except tk.TclError:
            pass
        self.progress_bar.configure(mode="determinate", maximum=1, value=0)
        self.status_var.set("Ready.")

    def _set_progress(self, cur: int, total: int, text: str) -> None:
        self.status_var.set(text)
        if total > 0:
            if str(self.progress_bar["mode"]) == "indeterminate":
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate")
            self.progress_bar.configure(maximum=total, value=min(cur, total))
        else:
            if str(self.progress_bar["mode"]) != "indeterminate":
                self.progress_bar.configure(mode="indeterminate")
                self.progress_bar.start(15)

    # ---------------------------------------------------------- Folder select

    def select_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select the folder to consolidate")
        if not selected:
            return
        self.root_folder = Path(selected).resolve()
        self.folder_var.set(str(self.root_folder))
        self.clear_scan()

    # ---------------------------------------------------------- Scan

    def scan(self) -> None:
        if self.root_folder is None:
            messagebox.showinfo("Select folder", "Select a root folder first.")
            return
        if not self.root_folder.exists():
            messagebox.showerror("Folder not found", "The selected root folder no longer exists.")
            return
        if not self.suffixes:
            messagebox.showinfo("No file types", "Select at least one file extension in the File Types tab.")
            return

        self.clear_scan()
        self._reset_progress()
        self._set_busy(True, "scan")

        threading.Thread(
            target=self._scan_worker,
            args=(self.root_folder, set(self.suffixes), self.msg_queue, self.cancel_event),
            daemon=True,
        ).start()
        self.after(50, self._poll_queue)

    def _scan_worker(
        self,
        root: Path,
        suffixes: Set[str],
        msg_queue: "queue.Queue[tuple]",
        cancel_event: threading.Event,
    ) -> None:
        try:
            scanner = Scanner()
            result = scanner.scan(
                root=root,
                suffixes=suffixes,
                progress_cb=lambda c, t, txt: msg_queue.put(("progress", c, t, txt)),
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                msg_queue.put(("cancelled", "Scan cancelled. No files were changed."))
                return
            msg_queue.put(("scan_done", result))
        except AbortException as exc:
            msg_queue.put(("cancelled", str(exc)))
        except Exception as exc:
            msg_queue.put(("error", (str(exc), None, None, None)))

    # ---------------------------------------------------------- Group / variant interactions

    def refresh_group_tree(self) -> None:
        filter_text = self.filter_var.get().strip().casefold()
        conflicts_only = self.conflicts_only_var.get()

        visible: List[str] = []
        for key in self.all_group_keys:
            variants = self.groups[key]
            if conflicts_only and len(variants) <= 1:
                continue
            if filter_text and filter_text not in variants[0].path.name.casefold():
                continue
            visible.append(key)

        self.visible_group_keys = visible
        self.group_tree.delete(*self.group_tree.get_children())
        for key in visible:
            variants = self.groups[key]
            status = "Conflict" if len(variants) > 1 else "Unique"
            self.group_tree.insert("", "end", iid=key, text=variants[0].path.name,
                                   values=(len(variants), status))

        if self.current_key and self.current_key not in visible:
            self.current_key = None
            self.variant_tree.delete(*self.variant_tree.get_children())
            self.preview_label.configure(image="", text="Select a variant to preview.")

    def on_group_selected(self, _event=None) -> None:
        if self.is_busy:
            return
        selected = self.group_tree.selection()
        if not selected:
            return
        key = selected[0]
        if key not in self.groups:
            return
        self.current_key = key
        self.variant_tree.delete(*self.variant_tree.get_children())
        retained = self.selections[key]
        for index, record in enumerate(self.groups[key]):
            marker = "YES" if record.path == retained else ""
            self.variant_tree.insert(
                "", "end", iid=str(index),
                values=(marker, record.modified_text,
                        self.format_size(record.size_bytes), str(record.relative_path)),
            )
        selection_index = next(
            (i for i, r in enumerate(self.groups[key]) if r.path == retained), 0
        )
        self.variant_tree.selection_set(str(selection_index))
        self.variant_tree.focus(str(selection_index))
        self.on_variant_highlighted()

    def on_variant_highlighted(self, _event=None) -> None:
        if self.is_busy or self.current_key is None:
            return
        selected = self.variant_tree.selection()
        if not selected:
            return
        record = self.groups[self.current_key][int(selected[0])]
        if Image is None:
            self.preview_label.configure(
                image="",
                text=f"{record.relative_path}\n\nInstall Pillow for thumbnails:\n"
                     "python -m pip install Pillow",
            )
            return
        try:
            with Image.open(record.path) as source:
                image = source.copy()
            image.thumbnail((390, 440))
            self.preview_image = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self.preview_image, text="")
        except Exception as exc:
            self.preview_label.configure(
                image="", text=f"Preview unavailable\n{exc}"
            )

    def retain_highlighted(self, _event=None) -> None:
        if self.is_busy or self.current_key is None:
            return
        selected = self.variant_tree.selection()
        if not selected:
            return
        record = self.groups[self.current_key][int(selected[0])]
        self.selections[self.current_key] = record.path
        self.on_group_selected()

    # ---------------------------------------------------------- Auto-select

    @staticmethod
    def _pick_newest(variants: List[ImageRecord]) -> ImageRecord:
        return max(variants, key=lambda r: (r.modified_ns, r.size_bytes))

    @staticmethod
    def _pick_largest(variants: List[ImageRecord]) -> ImageRecord:
        return max(variants, key=lambda r: (r.size_bytes, r.modified_ns))

    @staticmethod
    def _pick_shortest_path(variants: List[ImageRecord]) -> ImageRecord:
        return min(
            variants,
            key=lambda r: (
                len(r.relative_path.parts),
                len(str(r.relative_path)),
                str(r.relative_path).casefold(),
            ),
        )

    def _rule_fn(self, rule: str):
        return {
            "newest": self._pick_newest,
            "largest": self._pick_largest,
            "shortest": self._pick_shortest_path,
        }[rule]

    def apply_rule(self, rule: str, only_conflicts: bool = True) -> None:
        if self.is_busy or not self.groups:
            return
        fn = self._rule_fn(rule)

        changed = 0
        for key, variants in self.groups.items():
            if only_conflicts and len(variants) <= 1:
                continue
            chosen = fn(variants)
            if self.selections.get(key) != chosen.path:
                changed += 1
            self.selections[key] = chosen.path

        if changed == 0:
            self.status_var.set("Auto-select applied no changes.")
            return

        scope = "conflict groups" if only_conflicts else "all groups"
        ok = messagebox.askyesno(
            "Apply auto-select",
            f"This will change the retained variant in {changed} {scope}.\n\nProceed?",
        )
        if not ok:
            # Revert by re-running with a fresh scan? We cannot undo safely, so
            # we simply warn that selections were already modified.
            messagebox.showinfo(
                "Auto-select",
                "Selections were already updated before confirmation and cannot be "
                "reverted automatically. Use 'Reset all to newest' or pick variants "
                "manually to correct.",
            )

        # Refresh visible tree statuses
        self.refresh_group_tree()
        if self.current_key and self.current_key in self.groups:
            self.on_group_selected()

    # ---------------------------------------------------------- Execute

    def execute(self) -> None:
        if self.root_folder is None or not self.records:
            return
        root = self.root_folder

        # Same-filesystem warning (warn only)
        if not same_filesystem(root, root.parent):
            proceed = messagebox.askyesno(
                "Different filesystems",
                "The selected folder and its parent are on different filesystems.\n\n"
                "Staging and quarantine will be created on the parent filesystem, so file "
                "moves will fall back to non-atomic copy+delete. This is slower and cannot "
                "be interrupted as safely.\n\nProceed?",
                icon="warning",
            )
            if not proceed:
                return

        retained_records: List[ImageRecord] = []
        for key, selected_path in self.selections.items():
            variants = self.groups.get(key, [])
            record = next((r for r in variants if r.path == selected_path), None)
            if record is not None:
                retained_records.append(record)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        parent = root.parent
        staging = parent / f"{APP_PREFIX}staging_{root.name}_{stamp}"
        quarantine = parent / f"{APP_PREFIX}quarantine_{root.name}_{stamp}"
        manifest = parent / f"{APP_PREFIX}manifest_{root.name}_{stamp}.csv"

        response = messagebox.askyesno(
            "Execute consolidation",
            f"Retain {len(retained_records):,} images at the top level of:\n{root}\n\n"
            f"Move all other content to:\n{quarantine}\n\nProceed?",
            icon="warning",
        )
        if not response:
            return

        self._reset_progress()
        self._set_busy(True, "execute")

        threading.Thread(
            target=self._execute_worker,
            args=(root, retained_records, staging, quarantine, manifest,
                  self.msg_queue, self.cancel_event, self.pause_event),
            daemon=True,
        ).start()
        self.after(50, self._poll_queue)

    def _execute_worker(
        self,
        root: Path,
        retained_records: List[ImageRecord],
        staging: Path,
        quarantine: Path,
        manifest: Path,
        msg_queue: "queue.Queue[tuple]",
        cancel_event: threading.Event,
        pause_event: threading.Event,
    ) -> None:
        try:
            executor = Executor()
            result = executor.execute(
                root=root,
                retained_records=retained_records,
                staging=staging,
                quarantine=quarantine,
                manifest=manifest,
                progress_cb=lambda c, t, txt: msg_queue.put(("progress", c, t, txt)),
                cancel_event=cancel_event,
                pause_event=pause_event,
            )
            if result.aborted:
                msg_queue.put(("cancelled",
                               f"Aborted. Partial manifest written to:\n{manifest}"))
            else:
                msg_queue.put(("exec_done", result))
        except Exception as exc:
            msg_queue.put(("error", (str(exc), str(staging), str(quarantine), str(manifest))))

    # ---------------------------------------------------------- Pause / Abort / Close

    def toggle_pause(self) -> None:
        if not self.is_busy or self.operation != "execute":
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="Pause")
            self.status_var.set("Resuming…")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="Resume")
            self.status_var.set("Paused. Click Resume to continue.")

    def abort(self) -> None:
        if not self.is_busy:
            return
        if not messagebox.askyesno(
            "Abort operation",
            "Abort the current operation?\n\n"
            "For execute: logs will be finalized and no further files will be touched. "
            "Staging and quarantine are preserved for manual recovery.\n\n"
            "For scan: the scan stops immediately. No files are changed.",
            icon="warning",
        ):
            return
        self.cancel_event.set()
        self.pause_event.clear()  # wake up a paused worker
        self.status_var.set("Aborting…")

    def on_close(self) -> None:
        if self.is_busy:
            if not messagebox.askyesno(
                "Operation in progress",
                "An operation is in progress. Abort it and close?",
                icon="warning",
            ):
                return
            self.cancel_event.set()
            self.pause_event.clear()
            # Give the worker a brief moment to unwind and write the manifest.
            self.after(400, self.destroy)
        else:
            self.destroy()

    # ---------------------------------------------------------- Queue poll

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self._dispatch(msg)
        except queue.Empty:
            pass
        if self.is_busy:
            self.after(50, self._poll_queue)

    def _dispatch(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "progress":
            _, cur, total, text = msg
            self._set_progress(cur, total, text)
        elif kind == "scan_done":
            self._on_scan_done(msg[1])
        elif kind == "exec_done":
            self._on_exec_done(msg[1])
        elif kind == "error":
            self._on_error(msg[1])
        elif kind == "cancelled":
            self._on_cancelled(msg[1])

    # ---------------------------------------------------------- Queue handlers

    def _on_scan_done(self, result: ScanResult) -> None:
        self._set_busy(False)
        self._reset_progress()

        self.records = result.records
        self.groups = result.groups
        self.selections = result.selections
        self.all_group_keys = sorted(
            result.groups.keys(),
            key=lambda k: (len(result.groups[k]) == 1, k),
        )
        self.current_key = None
        self.variant_tree.delete(*self.variant_tree.get_children())
        self.preview_label.configure(image="", text="Select a variant to preview.")

        conflicts = sum(1 for v in result.groups.values() if len(v) > 1)
        self.summary_var.set(
            f"Found {len(result.records):,} images, {len(result.groups):,} output filenames, "
            f"and {conflicts:,} conflict groups. Inaccessible images skipped: "
            f"{result.inaccessible:,}."
        )

        self.refresh_group_tree()

        if self.records:
            first_children = self.group_tree.get_children()
            if first_children:
                first = first_children[0]
                self.group_tree.selection_set(first)
                self.group_tree.focus(first)
                self.on_group_selected()
        else:
            self._update_control_states()
            messagebox.showinfo(
                "No images",
                "No images were found matching the selected file types.",
            )
            return

        self._update_control_states()

    def _on_exec_done(self, result: ExecResult) -> None:
        self._set_busy(False)
        self._reset_progress()

        self.last_root = self.root_folder
        self.last_quarantine = result.quarantine
        self.last_manifest = result.manifest

        self.clear_scan()
        self.summary_var.set(f"Complete. Quarantine: {result.quarantine} | Manifest: {result.manifest}")
        self.status_var.set("Consolidation complete.")
        self._update_control_states()

        messagebox.showinfo(
            "Consolidation complete",
            f"Retained images are in the root.\n\n"
            f"Quarantine:\n{result.quarantine}\n\n"
            f"Manifest:\n{result.manifest}",
        )

    def _on_error(self, payload: tuple) -> None:
        message, staging, quarantine, manifest = payload
        self._set_busy(False)
        self._reset_progress()
        self.status_var.set("Operation stopped.")
        self._update_control_states()

        # Remember paths for the Open buttons so the user can inspect.
        if self.root_folder is not None:
            self.last_root = self.root_folder
        if quarantine:
            self.last_quarantine = Path(quarantine)
        if manifest:
            self.last_manifest = Path(manifest)
        self._update_control_states()

        detail = message
        extras = []
        if staging:
            extras.append(f"Staging:\n{staging}")
        if quarantine:
            extras.append(f"Quarantine:\n{quarantine}")
        if manifest:
            extras.append(f"Manifest:\n{manifest}")
        if extras:
            detail += "\n\nInspect these locations before retrying:\n" + "\n".join(extras)

        messagebox.showerror(
            "Operation stopped",
            "The operation stopped without permanently deleting files.\n\n" + detail,
        )

    def _on_cancelled(self, message: str) -> None:
        self._set_busy(False)
        self._reset_progress()
        self.status_var.set(message)
        self._update_control_states()

    # ---------------------------------------------------------- Open buttons

    def open_root(self) -> None:
        self._safe_open(self.last_root)

    def open_quarantine(self) -> None:
        self._safe_open(self.last_quarantine)

    def open_manifest_folder(self) -> None:
        if self.last_manifest is None:
            return
        self._safe_open(self.last_manifest.parent)

    def _safe_open(self, path: Optional[Path]) -> None:
        if path is None:
            return
        try:
            open_in_file_manager(path)
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))

    # ---------------------------------------------------------- Small helpers

    @staticmethod
    def format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"


if __name__ == "__main__":
    PhotoConsolidatorApp().mainloop()
