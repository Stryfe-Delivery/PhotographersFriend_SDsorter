#!/usr/bin/env python3
"""Photo Consolidator

Recursively collects .png/.jpg/.jpeg images into a selected folder's top level.
Filename conflicts are reviewed by the user, with the newest variant selected by default.
All non-retained content is moved to a timestamped quarantine folder outside the root.

Optional dependency for thumbnails:
    python -m pip install Pillow
"""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
APP_PREFIX = "_photo_consolidator_"


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


class PhotoConsolidatorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Photo Consolidator")
        self.geometry("1180x760")
        self.minsize(900, 620)

        self.root_folder: Optional[Path] = None
        self.records: List[ImageRecord] = []
        self.groups: Dict[str, List[ImageRecord]] = {}
        self.selections: Dict[str, Path] = {}
        self.group_keys: List[str] = []
        self.current_key: Optional[str] = None
        self.preview_image = None

        self._build_ui()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Select Folder", command=self.select_folder).pack(side="left")
        ttk.Button(toolbar, text="Scan", command=self.scan).pack(side="left", padx=(8, 0))
        self.execute_button = ttk.Button(toolbar, text="Execute Consolidation", command=self.execute, state="disabled")
        self.execute_button.pack(side="left", padx=(8, 0))

        self.folder_var = tk.StringVar(value="No folder selected")
        ttk.Label(toolbar, textvariable=self.folder_var).pack(side="left", padx=14, fill="x", expand=True)

        self.summary_var = tk.StringVar(value="Select a folder, then scan. No files are changed during scanning.")
        ttk.Label(self, textvariable=self.summary_var, padding=(12, 0, 12, 10)).pack(fill="x")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

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
        self.variant_tree = ttk.Treeview(variant_frame, columns=columns, show="headings", selectmode="browse")
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

        ttk.Button(preview_frame, text="Retain Highlighted Variant", command=self.retain_highlighted).pack(fill="x")
        self.preview_label = ttk.Label(preview_frame, text="Select a variant to preview.", anchor="center", justify="center")
        self.preview_label.pack(fill="both", expand=True, pady=(10, 0))

        note = (
            "Execution is destructive to the selected root layout, but not an immediate permanent deletion. "
            "Retained images are staged, all other content is moved to a sibling quarantine folder, "
            "then retained images are promoted to the root."
        )
        ttk.Label(self, text=note, wraplength=1120, padding=(12, 0, 12, 10)).pack(fill="x")

    def select_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select the folder to consolidate")
        if not selected:
            return
        self.root_folder = Path(selected).resolve()
        self.folder_var.set(str(self.root_folder))
        self.clear_scan()

    def clear_scan(self) -> None:
        self.records.clear()
        self.groups.clear()
        self.selections.clear()
        self.group_keys.clear()
        self.current_key = None
        self.group_tree.delete(*self.group_tree.get_children())
        self.variant_tree.delete(*self.variant_tree.get_children())
        self.preview_label.configure(image="", text="Select a variant to preview.")
        self.execute_button.configure(state="disabled")
        self.summary_var.set("Folder selected. Click Scan. No files are changed during scanning.")

    def scan(self) -> None:
        if self.root_folder is None:
            messagebox.showinfo("Select folder", "Select a root folder first.")
            return
        if not self.root_folder.exists():
            messagebox.showerror("Folder not found", "The selected root folder no longer exists.")
            return

        self.clear_scan()
        inaccessible = 0
        for current, dirnames, filenames in os.walk(self.root_folder, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(APP_PREFIX)]
            current_path = Path(current)
            for filename in filenames:
                path = current_path / filename
                if path.suffix.lower() not in IMAGE_SUFFIXES or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                    relative = path.relative_to(self.root_folder)
                    record = ImageRecord(
                        path=path,
                        relative_path=relative,
                        name_key=path.name.casefold(),
                        modified_ns=stat.st_mtime_ns,
                        size_bytes=stat.st_size,
                    )
                    self.records.append(record)
                except OSError:
                    inaccessible += 1

        for record in self.records:
            self.groups.setdefault(record.name_key, []).append(record)
        for key, variants in self.groups.items():
            variants.sort(key=lambda r: (r.modified_ns, r.size_bytes, str(r.relative_path).casefold()), reverse=True)
            self.selections[key] = variants[0].path

        self.group_keys = sorted(self.groups, key=lambda k: (len(self.groups[k]) == 1, k))
        for key in self.group_keys:
            variants = self.groups[key]
            status = "Conflict" if len(variants) > 1 else "Unique"
            self.group_tree.insert("", "end", iid=key, text=variants[0].path.name, values=(len(variants), status))

        conflicts = sum(1 for variants in self.groups.values() if len(variants) > 1)
        self.summary_var.set(
            f"Found {len(self.records):,} images, {len(self.groups):,} output filenames, "
            f"and {conflicts:,} conflict groups. Inaccessible images skipped: {inaccessible:,}."
        )
        if self.records:
            self.execute_button.configure(state="normal")
            first = self.group_tree.get_children()[0]
            self.group_tree.selection_set(first)
            self.group_tree.focus(first)
            self.on_group_selected()
        else:
            messagebox.showinfo("No images", "No .png, .jpg, or .jpeg images were found.")

    def on_group_selected(self, _event=None) -> None:
        selected = self.group_tree.selection()
        if not selected:
            return
        self.current_key = selected[0]
        self.variant_tree.delete(*self.variant_tree.get_children())
        retained = self.selections[self.current_key]
        for index, record in enumerate(self.groups[self.current_key]):
            marker = "YES" if record.path == retained else ""
            self.variant_tree.insert(
                "", "end", iid=str(index),
                values=(marker, record.modified_text, self.format_size(record.size_bytes), str(record.relative_path)),
            )
        selection_index = next(i for i, record in enumerate(self.groups[self.current_key]) if record.path == retained)
        self.variant_tree.selection_set(str(selection_index))
        self.variant_tree.focus(str(selection_index))
        self.on_variant_highlighted()

    def on_variant_highlighted(self, _event=None) -> None:
        if self.current_key is None:
            return
        selected = self.variant_tree.selection()
        if not selected:
            return
        record = self.groups[self.current_key][int(selected[0])]
        if Image is None:
            self.preview_label.configure(image="", text=f"{record.relative_path}\n\nInstall Pillow for thumbnails:\npython -m pip install Pillow")
            return
        try:
            with Image.open(record.path) as source:
                image = source.copy()
            image.thumbnail((390, 440))
            self.preview_image = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self.preview_image, text="")
        except Exception as exc:
            self.preview_label.configure(image="", text=f"Preview unavailable\n{exc}")

    def retain_highlighted(self, _event=None) -> None:
        if self.current_key is None:
            return
        selected = self.variant_tree.selection()
        if not selected:
            return
        record = self.groups[self.current_key][int(selected[0])]
        self.selections[self.current_key] = record.path
        self.on_group_selected()

    def execute(self) -> None:
        if self.root_folder is None or not self.records:
            return
        root = self.root_folder
        parent = root.parent
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        staging = parent / f"{APP_PREFIX}staging_{root.name}_{stamp}"
        quarantine = parent / f"{APP_PREFIX}quarantine_{root.name}_{stamp}"
        manifest = parent / f"{APP_PREFIX}manifest_{root.name}_{stamp}.csv"

        retained_records = [next(r for r in self.groups[key] if r.path == selected) for key, selected in self.selections.items()]
        selected_paths = {r.path for r in retained_records}

        response = messagebox.askyesno(
            "Execute consolidation",
            f"Retain {len(retained_records):,} images at the top level of:\n{root}\n\n"
            f"Move all other content to:\n{quarantine}\n\nProceed?",
            icon="warning",
        )
        if not response:
            return

        rows = []
        try:
            staging.mkdir(parents=False, exist_ok=False)
            quarantine.mkdir(parents=False, exist_ok=False)

            # Phase 1: move retained images to sibling staging and verify each move.
            for record in retained_records:
                destination = staging / record.path.name
                if destination.exists():
                    raise FileExistsError(f"Unexpected staging collision: {destination.name}")
                source_hash = self.sha256(record.path)
                shutil.move(str(record.path), str(destination))
                if self.sha256(destination) != source_hash:
                    raise OSError(f"Verification failed after staging: {record.relative_path}")
                rows.append(["KEEP", str(record.relative_path), str(destination), source_hash])

            # Phase 2a: quarantine every remaining root entry while preserving its tree.
            for child in list(root.iterdir()):
                destination = quarantine / child.name
                shutil.move(str(child), str(destination))
                rows.append(["QUARANTINE", str(child.relative_to(root)), str(destination), ""])

            # Phase 2b: promote retained files into the now-empty root.
            for staged_file in list(staging.iterdir()):
                destination = root / staged_file.name
                shutil.move(str(staged_file), str(destination))
                rows.append(["PROMOTE", str(staged_file), str(destination), self.sha256(destination)])
            staging.rmdir()

            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["action", "source", "destination", "sha256"])
                writer.writerows(rows)

            self.clear_scan()
            self.summary_var.set(f"Complete. Quarantine: {quarantine} | Manifest: {manifest}")
            messagebox.showinfo("Consolidation complete", f"Retained images are in the root.\n\nQuarantine:\n{quarantine}\n\nManifest:\n{manifest}")
        except Exception as exc:
            # Preserve staging/quarantine for manual recovery; never auto-delete after failure.
            try:
                with manifest.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["action", "source", "destination", "sha256"])
                    writer.writerows(rows)
                    writer.writerow(["ERROR", "", "", str(exc)])
            except OSError:
                pass
            messagebox.showerror(
                "Operation stopped",
                f"The operation stopped without permanently deleting files.\n\n{exc}\n\n"
                f"Inspect these locations before retrying:\n{staging}\n{quarantine}\n{manifest}",
            )

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

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
