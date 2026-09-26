#!/usr/bin/env python3
"""Photo Consolidator: flatten PNG/JPG images with conflict review and quarantine."""
from __future__ import annotations
import csv, hashlib, os, shutil, tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
APP_PREFIX = "_photo_consolidator_"

@dataclass(frozen=True)
class ImageRecord:
    path: Path
    relative_path: Path
    full_name_key: str
    stem_key: str
    modified_ns: int
    size_bytes: int
    @property
    def modified_text(self):
        return datetime.fromtimestamp(self.modified_ns / 1e9).strftime("%Y-%m-%d %H:%M:%S")

class PhotoConsolidatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Photo Consolidator")
        self.geometry("1220x760")
        self.minsize(940, 620)
        self.root_folder: Optional[Path] = None
        self.records = []
        self.groups = {}
        self.selections = {}
        self.current_key = None
        self.preview_image = None
        self._build_ui()

    def _build_ui(self):
        bar = ttk.Frame(self, padding=10); bar.pack(fill="x")
        ttk.Button(bar, text="Select Folder", command=self.select_folder).pack(side="left")
        ttk.Button(bar, text="Scan", command=self.scan).pack(side="left", padx=(8,0))
        self.ignore_extension_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Match filenames ignoring extension", variable=self.ignore_extension_var).pack(side="left", padx=(12,0))
        self.execute_button = ttk.Button(bar, text="Execute Consolidation", command=self.execute, state="disabled")
        self.execute_button.pack(side="left", padx=(12,0))
        self.folder_var = tk.StringVar(value="No folder selected")
        ttk.Label(bar, textvariable=self.folder_var).pack(side="left", padx=14, fill="x", expand=True)
        self.summary_var = tk.StringVar(value="Select a folder, then scan. Scanning does not change files.")
        ttk.Label(self, textvariable=self.summary_var, padding=(12,0,12,10)).pack(fill="x")

        pane = ttk.Panedwindow(self, orient="horizontal"); pane.pack(fill="both", expand=True, padx=10, pady=(0,10))
        left = ttk.Labelframe(pane, text="Filename Groups", padding=6)
        right = ttk.Labelframe(pane, text="Variants: select one to retain", padding=8)
        pane.add(left, weight=1); pane.add(right, weight=3)
        self.group_tree = ttk.Treeview(left, columns=("count","status"), show="tree headings")
        self.group_tree.heading("#0", text="Filename / stem"); self.group_tree.heading("count", text="Files"); self.group_tree.heading("status", text="Status")
        self.group_tree.column("#0", width=220); self.group_tree.column("count", width=55, anchor="center"); self.group_tree.column("status", width=80, anchor="center")
        self.group_tree.pack(side="left", fill="both", expand=True)
        gs=ttk.Scrollbar(left, command=self.group_tree.yview); gs.pack(side="right", fill="y"); self.group_tree.configure(yscrollcommand=gs.set)
        self.group_tree.bind("<<TreeviewSelect>>", self.on_group_selected)

        right.rowconfigure(0, weight=1); right.columnconfigure(0, weight=3); right.columnconfigure(1, weight=2)
        vf=ttk.Frame(right); vf.grid(row=0,column=0,sticky="nsew")
        pf=ttk.Frame(right,padding=(12,0,0,0)); pf.grid(row=0,column=1,sticky="nsew")
        cols=("retain","extension","modified","size","path")
        self.variant_tree=ttk.Treeview(vf,columns=cols,show="headings",selectmode="browse")
        for col,title in zip(cols,("Retain","Ext","Modified","Size","Relative path")): self.variant_tree.heading(col,text=title)
        self.variant_tree.column("retain",width=60,anchor="center"); self.variant_tree.column("extension",width=55,anchor="center")
        self.variant_tree.column("modified",width=145); self.variant_tree.column("size",width=85,anchor="e"); self.variant_tree.column("path",width=350)
        self.variant_tree.pack(side="left",fill="both",expand=True)
        vs=ttk.Scrollbar(vf,command=self.variant_tree.yview); vs.pack(side="right",fill="y"); self.variant_tree.configure(yscrollcommand=vs.set)
        self.variant_tree.bind("<<TreeviewSelect>>",self.on_variant_highlighted); self.variant_tree.bind("<Double-1>",self.retain_highlighted)
        ttk.Button(pf,text="Retain Highlighted Variant",command=self.retain_highlighted).pack(fill="x")
        self.preview_label=ttk.Label(pf,text="Select a variant to preview.",anchor="center",justify="center")
        self.preview_label.pack(fill="both",expand=True,pady=(10,0))
        ttk.Label(self,text="Retained images are verified in staging. Everything else is moved to a sibling quarantine folder, not permanently deleted.",wraplength=1160,padding=(12,0,12,10)).pack(fill="x")

    def select_folder(self):
        selected=filedialog.askdirectory(title="Select the folder to consolidate")
        if selected:
            self.root_folder=Path(selected).resolve(); self.folder_var.set(str(self.root_folder)); self.clear_scan()

    def clear_scan(self):
        self.records=[]; self.groups={}; self.selections={}; self.current_key=None
        self.group_tree.delete(*self.group_tree.get_children()); self.variant_tree.delete(*self.variant_tree.get_children())
        self.preview_label.configure(image="",text="Select a variant to preview."); self.execute_button.configure(state="disabled")
        self.summary_var.set("Folder selected. Click Scan. Scanning does not change files.")

    def scan(self):
        if not self.root_folder:
            messagebox.showinfo("Select folder","Select a root folder first."); return
        if not self.root_folder.exists():
            messagebox.showerror("Folder not found","The selected folder no longer exists."); return
        self.clear_scan(); inaccessible=0
        for current,dirs,files in os.walk(self.root_folder,followlinks=False):
            dirs[:]=[d for d in dirs if not d.startswith(APP_PREFIX)]
            for name in files:
                path=Path(current)/name
                if path.suffix.lower() not in IMAGE_SUFFIXES or path.is_symlink(): continue
                try:
                    st=path.stat(); rel=path.relative_to(self.root_folder)
                    self.records.append(ImageRecord(path,rel,path.name.casefold(),path.stem.casefold(),st.st_mtime_ns,st.st_size))
                except OSError: inaccessible += 1
        by_stem=self.ignore_extension_var.get()
        for r in self.records:
            key=r.stem_key if by_stem else r.full_name_key
            self.groups.setdefault(key,[]).append(r)
        for key,items in self.groups.items():
            items.sort(key=lambda r:(r.modified_ns,r.size_bytes,str(r.relative_path).casefold()),reverse=True)
            self.selections[key]=items[0].path
        for key in sorted(self.groups,key=lambda k:(len(self.groups[k])==1,k)):
            items=self.groups[key]; status="Conflict" if len(items)>1 else "Unique"
            label=items[0].path.stem if by_stem else items[0].path.name
            self.group_tree.insert("","end",iid=key,text=label,values=(len(items),status))
        conflicts=sum(len(v)>1 for v in self.groups.values()); mode="ignore extension" if by_stem else "full filename"
        self.summary_var.set(f"Found {len(self.records):,} images, {len(self.groups):,} filename groups, and {conflicts:,} conflict groups (matching mode: {mode}). Inaccessible images skipped: {inaccessible:,}.")
        if self.records:
            self.execute_button.configure(state="normal"); first=self.group_tree.get_children()[0]; self.group_tree.selection_set(first); self.on_group_selected()
        else: messagebox.showinfo("No images","No .png, .jpg, or .jpeg images were found.")

    def on_group_selected(self,_event=None):
        selected=self.group_tree.selection()
        if not selected:return
        self.current_key=selected[0]; self.variant_tree.delete(*self.variant_tree.get_children()); retained=self.selections[self.current_key]
        for i,r in enumerate(self.groups[self.current_key]):
            self.variant_tree.insert("","end",iid=str(i),values=("YES" if r.path==retained else "",r.path.suffix.lower(),r.modified_text,self.format_size(r.size_bytes),str(r.relative_path)))
        i=next(i for i,r in enumerate(self.groups[self.current_key]) if r.path==retained)
        self.variant_tree.selection_set(str(i)); self.on_variant_highlighted()

    def on_variant_highlighted(self,_event=None):
        selected=self.variant_tree.selection()
        if self.current_key is None or not selected:return
        r=self.groups[self.current_key][int(selected[0])]
        if Image is None:
            self.preview_label.configure(image="",text=f"{r.relative_path}\n\nInstall Pillow for thumbnails:\npython -m pip install Pillow"); return
        try:
            with Image.open(r.path) as src: image=src.copy()
            image.thumbnail((390,440)); self.preview_image=ImageTk.PhotoImage(image); self.preview_label.configure(image=self.preview_image,text="")
        except Exception as exc: self.preview_label.configure(image="",text=f"Preview unavailable\n{exc}")

    def retain_highlighted(self,_event=None):
        selected=self.variant_tree.selection()
        if self.current_key is None or not selected:return
        self.selections[self.current_key]=self.groups[self.current_key][int(selected[0])].path; self.on_group_selected()

    def execute(self):
        if not self.root_folder or not self.records:return
        root=self.root_folder; stamp=datetime.now().strftime("%Y%m%d_%H%M%S"); parent=root.parent
        staging=parent/f"{APP_PREFIX}staging_{root.name}_{stamp}"; quarantine=parent/f"{APP_PREFIX}quarantine_{root.name}_{stamp}"; manifest=parent/f"{APP_PREFIX}manifest_{root.name}_{stamp}.csv"
        retained=[next(r for r in self.groups[k] if r.path==p) for k,p in self.selections.items()]
        if not messagebox.askyesno("Execute consolidation",f"Retain {len(retained):,} images at the top level of:\n{root}\n\nMove everything else to:\n{quarantine}\n\nProceed?",icon="warning"):return
        rows=[]
        try:
            staging.mkdir(); quarantine.mkdir()
            for r in retained:
                dst=staging/r.path.name
                if dst.exists(): raise FileExistsError(f"Unexpected output collision: {dst.name}")
                digest=self.sha256(r.path); shutil.move(str(r.path),str(dst))
                if self.sha256(dst)!=digest: raise OSError(f"Verification failed: {r.relative_path}")
                rows.append(["KEEP",str(r.relative_path),str(dst),digest])
            for child in list(root.iterdir()):
                dst=quarantine/child.name; shutil.move(str(child),str(dst)); rows.append(["QUARANTINE",str(child.relative_to(root)),str(dst),""])
            for src in list(staging.iterdir()):
                dst=root/src.name; shutil.move(str(src),str(dst)); rows.append(["PROMOTE",str(src),str(dst),self.sha256(dst)])
            staging.rmdir(); self.write_manifest(manifest,rows); self.clear_scan()
            self.summary_var.set(f"Complete. Quarantine: {quarantine} | Manifest: {manifest}")
            messagebox.showinfo("Complete",f"Retained images are in the root.\n\nQuarantine:\n{quarantine}\n\nManifest:\n{manifest}")
        except Exception as exc:
            try: self.write_manifest(manifest,rows+[["ERROR","","",str(exc)]])
            except OSError: pass
            messagebox.showerror("Operation stopped",f"No files were permanently deleted.\n\n{exc}\n\nInspect:\n{staging}\n{quarantine}\n{manifest}")

    @staticmethod
    def write_manifest(path,rows):
        with path.open("w",newline="",encoding="utf-8") as f:
            w=csv.writer(f); w.writerow(["action","source","destination","sha256"]); w.writerows(rows)
    @staticmethod
    def sha256(path):
        h=hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
        return h.hexdigest()
    @staticmethod
    def format_size(size):
        value=float(size)
        for unit in ("B","KB","MB","GB","TB"):
            if value<1024 or unit=="TB":return f"{value:.1f} {unit}"
            value/=1024

if __name__ == "__main__":
    PhotoConsolidatorApp().mainloop()
