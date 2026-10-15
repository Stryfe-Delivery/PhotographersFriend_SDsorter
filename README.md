# Photo Consolidator

> A bespoke desktop tool for flattening a chaotic photo tree into a single, deduplicated, conflict-reviewed folder — with staging, quarantine, and a full audit manifest.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#requirements)
[![Tkinter](https://img.shields.io/badge/UI-Tkinter-informational.svg)](https://docs.python.org/3/library/tkinter.html)
[![Status](https://img.shields.io/badge/status-bespoke%20%2F%20maintained-brightgreen.svg)](#status--scope)
[![PRs](https://img.shields.io/badge/PRs-welcome-success.svg)](#contributing)

---

## Why this exists

I shoot a lot, and I edit in rounds. Over a couple of years I ended up with the same shoot scattered across a laptop, an external drive, two SD cards, and three "final_final_v2" folders. Filenames like `IMG_0421.JPG` and `DSC_0421.NEF` and `0421-edit.jpg` collided constantly — sometimes they were genuinely different photos, sometimes they were the same photo at different stages.

That last part matters more than it sounds. When you swap SD cards, reset a camera, or copy from two backups into one place, you inevitably get files with the same name and often the same size but different content. In a classic Windows environment the comparison info is either hidden in a properties dialog or so cumbersome to review file-by-file that people give up and just overwrite. The result is silent data loss.

I didn't want a photo library. I didn't want faces, albums, tags, or a database. I wanted to point a tool at one folder, let it walk the whole tree, and end up with:

- One copy of each filename at the top level.
- Every legitimate name collision surfaced and reviewed by me, not silently overwritten.
- Everything else moved to a quarantine folder I could triage later, in one place, with a CSV telling me exactly what happened.
- The ability to abort without losing data.

That's all. It's a single-task tool. It does one thing. If you need catalog management, use something else — see [Status & Scope](#status--scope).

The quarantine is the point: it isn't a trash folder. It's a holding area you can offload onto a spare drive, or delete outright, *once you've verified the retained versions and imported them into your normal processing pipeline*. The tool never deletes anything for you.

---

## What it does

Given a root folder, Photo Consolidator:

1. **Recursively scans** for image files matching a configurable set of extensions.
2. **Groups files by case-insensitive filename** — every `IMG_0421.jpg` in the tree becomes one group.
3. **Pre-selects the newest variant** in each group (by modified time, then size, then path).
4. **Lets you review conflicts** in a UI with thumbnails, filters, and bulk auto-select rules.
5. On execute, **stages** the retained files, **quarantines** everything else to a sibling folder, then **promotes** the retained files back into the now-empty root.
6. Writes a **CSV manifest** with SHA-256 hashes for every move.

Nothing is permanently deleted. Every action is logged. Aborting mid-run leaves staging and quarantine in place for manual recovery.

### The three-phase move

```
Before:
  root/
    a/IMG_0421.jpg
    b/IMG_0421.jpg          <- older duplicate
    c/DSC_0042.png
    d/vacation/DSC_0042.png <- older duplicate

Execute:

  Phase 1: stage retained files to  ../_photo_consolidator_staging_root_<ts>/
  Phase 2: quarantine everything else to ../_photo_consolidator_quarantine_root_<ts>/
  Phase 3: promote staged files back into the (now empty) root

After:
  root/
    IMG_0421.jpg
    DSC_0042.png

  ../_photo_consolidator_quarantine_root_<ts>/
    b/IMG_0421.jpg
    d/vacation/DSC_0042.png

  ../_photo_consolidator_manifest_root_<ts>.csv
```

The staging and quarantine folders are created **beside** the selected root, not inside it. Ensure the root's parent location has enough free space and that you have write permission there before starting.

---

## Features

| Area | What you get |
|---|---|
| Scanning | Recursive walk, symlink skip, `_photo_consolidator_*` self-exclusion, per-file error tolerance |
| Grouping | Case-insensitive filename grouping, newest-first sorting, unique groups sorted after conflicts |
| Review UI | Group tree, variant tree with retained marker, Pillow thumbnail preview (optional), double-click to retain |
| Filtering | Text filter on filename, "show conflicts only" checkbox |
| Bulk selection | Auto-select newest / largest / shortest path for conflict groups; reset all to newest |
| File types | Checkbox set for `.png .jpg .jpeg .webp .gif .bmp .tiff .tif` plus raw extensions, custom comma-separated entry |
| Threading | Scan and execute run off the Tk main thread; UI stays responsive |
| Progress | Determinate progress bar with per-file status text |
| Controls | Pause (execute only), Abort (scan or execute), close-confirmation during operations |
| Safety | `os.replace` for atomic same-filesystem moves, `shutil.move` fallback on `EXDEV`, cross-filesystem warning before start |
| Audit | CSV manifest with `action, source, destination, sha256`; partial manifest written on abort or error |
| Recovery | Staging and quarantine preserved on any failure; "Open Root / Quarantine / Manifest Folder" buttons |

---

## Requirements

### Required

- **Python 3.9 or newer** (uses `from __future__ import annotations`).

  Check the installed version:

  ```bash
  python --version
  ```

  On systems where `python` points to an older version or is not available, try:

  ```bash
  python3 --version
  ```

- **Tkinter** — bundled with standard CPython installers on Windows and macOS. Some Linux distributions ship it as a separate package.

  Test whether Tkinter is available:

  ```bash
  python -m tkinter
  ```

  A small Tk window should open. If it does, Tkinter is installed correctly.

  Common Linux install commands:

  ```bash
  # Ubuntu / Debian
  sudo apt install python3-tk

  # Fedora
  sudo dnf install python3-tkinter

  # Arch Linux
  sudo pacman -S tk
  ```

### Recommended

- **Pillow** — provides JPG, PNG, and other thumbnail previews in the conflict-review interface.

  ```bash
  python -m pip install Pillow
  ```

  The application runs without Pillow, but image previews are disabled and the preview pane shows the file path and a hint instead.

### No additional runtime packages required

All other modules used by the application are from the Python standard library:

`csv`, `datetime`, `errno`, `hashlib`, `os`, `pathlib`, `queue`, `shutil`, `subprocess`, `sys`, `threading`, `time`, `tkinter`.

---

## Installation

```bash
git clone https://github.com/<you>/photo-consolidator.git
cd photo-consolidator
python -m pip install -r requirements.txt   # optional, only Pillow
python photo_consolidator.py
```

Or just download `photo_consolidator.py` and run it. It's a single file with no required dependencies.

---

## Files

```
photo-consolidator/
├── photo_consolidator.py
└── README.md
```

---

## Running the application

Open a terminal in the project folder and run:

```bash
python photo_consolidator.py
```

If required on your system, use:

```bash
python3 photo_consolidator.py
```

---

## Recommended first run

Test the application on a **copied sample folder** before using it on the full photo collection. A useful test structure is:

```
sample_photos/
├── photo01.jpg
├── notes.txt
├── trip_a/
│   ├── photo01.jpg
│   └── photo02.png
└── trip_b/
    ├── photo01.jpg
    └── metadata.json
```

This example creates a conflict group for `photo01.jpg`. The newest version is selected by default, but any displayed variant can be chosen for retention. The non-image files (`notes.txt`, `metadata.json`) will be moved to quarantine during execution, demonstrating that the tool is not limited to images when it clears the root.

---

## Usage

1. Start `photo_consolidator.py`.
2. Select **Select Folder**.
3. Choose the root folder to consolidate.
4. *(Optional)* Switch to the **File Types** tab and adjust which extensions are scanned.
5. Select **Scan**.
6. Review filename groups marked **Conflict**.
7. Highlight a variant to view its metadata and preview.
8. Select **Retain Highlighted Variant**, or double-click the desired variant. Use the filter, the conflicts-only checkbox, or the auto-select buttons to move faster.
9. Review the scan summary.
10. Select **Execute Consolidation**.
11. Confirm the staging and quarantine operation.
12. After completion, use the **Open Root / Open Quarantine / Open Manifest Folder** buttons to verify.

Scanning does **not** modify any files. Files are changed only after **Execute Consolidation** is selected and the confirmation dialog is accepted.

---

## Conflict selection logic

Filename groups are matched case-insensitively. For example, these names are treated as one conflict group:

```
Photo01.JPG
photo01.jpg
PHOTO01.jpg
```

The default retained version is determined by sorting variants using:

1. **Newest modification timestamp**
2. **Largest file size**
3. **Relative path** as a deterministic tie-breaker

The user may override the default before execution. Bulk rules apply to conflict groups only:

- **Newest** — max modified time
- **Largest** — max size
- **Shortest path** — fewest path segments, then shortest string
- **Reset all to newest** — reapplies the default across every group, not just conflicts

---

## The three-phase move in detail

The application intentionally avoids immediate permanent deletion. Execution is a sequence of three phases, each of which is individually verifiable.

### Phase 1 — Stage and verify retained images

- Each selected image is moved to a timestamped sibling staging folder.
- A SHA-256 hash is calculated **before** the move.
- The staged file is hashed **again** after the move.
- Processing stops if the hashes do not match.

Example staging folder:

```
_photo_consolidator_staging_Photos_20260916_145900
```

### Phase 2 — Quarantine everything else

- All content remaining inside the selected root is moved to a timestamped sibling quarantine folder.
- Directory structure is preserved inside quarantine.

Example quarantine folder:

```
_photo_consolidator_quarantine_Photos_20260916_145900
```

### Phase 3 — Promote retained images

- The staged retained images are moved into the now-empty root folder.
- Each promoted file is re-hashed and verified.
- The empty staging folder is removed.
- The quarantine folder is retained for manual review or deletion.

The staging and quarantine folders are created **beside** the selected root, not inside it. Ensure that the root folder's parent location has enough free space and that you have write permission there.

---

## Operation manifest

A CSV manifest is written beside the selected root folder.

Example:

```
_photo_consolidator_manifest_Photos_20260916_145900.csv
```

Manifest action types:

| Action | Meaning |
|---|---|
| `KEEP` | Selected image moved to staging |
| `QUARANTINE` | Remaining root entry moved to quarantine |
| `PROMOTE` | Staged image moved into the root |
| `ABORT` | Operation stopped by user; partial log preserved |
| `ERROR` | Operation stopped because an error occurred |

The manifest records source paths, destination paths, and SHA-256 hashes where applicable. On abort or error, the manifest is still written with everything completed up to that point — it is the recovery recipe.

---

## Safety notes

- The application changes the selected folder only after **explicit confirmation**.
- It does **not** permanently delete the quarantine folder. The user decides when to remove it.
- It does **not** automatically undo a partially completed operation. There is currently no one-click rollback.
- If processing stops, **preserve the staging folder, quarantine folder, and manifest** until the file locations have been reviewed.
- Do not select a folder currently being synchronized, indexed, backed up, or modified by another application.
- Close applications that may have the photos open before execution.
- Confirm that adequate free disk space is available in the selected root's parent location.
- Maintain an **independent backup** for valuable or irreplaceable images.
- Avoid disconnecting storage or interrupting the application during execution.

---

## Supported files

By default, the application retains:

```
.png
.jpg
.jpeg
.webp
.gif
.bmp
.tiff
.tif
```

Raw camera formats are available as optional checkboxes (`.cr2 .cr3 .nef .arw .dng .orf .rw2 .raf .pef .srw`), and any other extension can be added through the **File Types** tab's custom comma-separated field (for example `.heic,.avif`).

All other files and directories are moved to quarantine during execution.

Two caveats worth stating plainly:

- The application does **not** inspect image contents to determine whether a file extension is accurate. It trusts the extension.
- Raw formats and any format without a Pillow codec will **not** display thumbnails in the preview pane. They are still consolidated correctly; only the preview is limited. This is intentional — no raw decoder is bundled.

---

## Troubleshooting

### The application does not start

Confirm the file passes a syntax check:

```bash
python -m py_compile photo_consolidator.py
```

Then verify Tkinter:

```bash
python -m tkinter
```

A small window should open. If it does not, install Tkinter for your platform (see [Requirements](#requirements)).

### No thumbnail appears

Install Pillow:

```bash
python -m pip install Pillow
```

Restart the application afterward. If the format is a raw camera file (CR2, NEF, ARW, etc.), thumbnails will not display even with Pillow installed — this is expected.

### Permission denied

Confirm that your user account has permission to:

- Read every file under the selected root
- Create folders **beside** the selected root (for staging and quarantine)
- Move files out of and into the selected root
- Write the CSV manifest in the root's parent folder

### Files are on different filesystems or storage devices

The utility prefers `os.replace` (an atomic rename) when the source and destination are on the same filesystem, and falls back to `shutil.move` (copy + delete) when they are not. A warning is shown before execution if the selected root and its parent are on different devices. Cross-device moves are slower and cannot be interrupted as safely; if you can, keep the root and its parent on the same device.

### Large raw files or files on NAS/cloud drives

These may take substantially longer to process. Test the workflow using locally stored JPGs and scale up from there.

### The operation stops partway through

Do **not** delete or rename the generated staging, quarantine, or manifest items. Review the manifest first — it records the completed operations and the reported error. Staging and quarantine are the recovery locations.

---

## Design notes

- **All UI on the Tk main thread.** Workers are pure filesystem/CPU classes that communicate via a `queue.Queue`. No Tk call ever crosses a thread boundary.
- **`os.replace` first, `shutil.move` second.** Same-filesystem moves are atomic renames. Cross-filesystem moves fall back to copy+delete. The tool warns you before starting if it detects `root` and `root.parent` on different devices.
- **No silent overwrites.** Every destination is checked before the move. If it exists, the operation raises rather than clobbering.
- **No permanent deletion anywhere.** Quarantine exists precisely so that a wrong decision is recoverable. The manifest is the ledger.
- **Self-exclusion.** Folders prefixed with `_photo_consolidator_` are skipped during scan, so re-running the tool on a parent directory doesn't re-ingest your own quarantine.
- **Pause is execute-only.** Scan is read-only and fast; pausing it adds no value. Execute can pause between file moves.
- **Abort is a literal abort.** On execute, the current file move finishes, the manifest is finalized, and no further filesystem calls are made. No rollback is attempted — partial state is preserved for manual recovery.

---

## Status & Scope

This is a **bespoke tool built for a single large consolidation task** — hundreds of gigabytes spread across multiple drives, multiple cameras, and multiple rounds of edits. It is intentionally not a photo manager.

**Intentionally missing** (do not open issues asking for these):

- No library, catalog, or database.
- No albums, tags, ratings, faces, or keywords.
- No cloud sync, upload, or sharing.
- No editing, RAW development, or export presets.
- No deduplication by content hash (grouping is by filename only).
- No EXIF capture-time comparison.
- No "portfolio management" of any kind.

If you need any of the above, use Darktable, Lightroom, digiKam, PhotoPrism, or Immich. This tool exists for the flat, deterministic, reviewable job of *getting files out of a mess and into one folder without losing anything*.

It can absolutely be forked and extended for other one-off tasks — batch renames, format conversions, manifest-driven recovery — but the maintainer's scope is deliberately narrow.

---

## Potential next steps

These are the directions that would make the tool more useful beyond its original run. **Pull requests are welcome** for any of them.

- **Manifest-driven recovery.** Read a manifest CSV and reverse it: move `KEEP` files back to their original relative paths, move `QUARANTINE` files back from quarantine. The manifest already has everything needed; only the reverse executor is missing. This is the highest-value addition.
- **Dry-run mode.** Produce the manifest and plan without moving anything, for large runs you want to preview first.
- **Unit tests.** The `Scanner` and `Executor` classes are pure and testable with `tmp_path`. Highest-value targets: grouping, newest-default selection, `safe_move` same-fs and cross-fs, abort-mid-phase manifest contents.
- **Logging to file.** Replace ad-hoc status-bar updates with `logging` to a rotating file next to the manifest, so post-mortem inspection doesn't depend on a dismissed messagebox.
- **CLI mode.** An `argparse` entrypoint that reuses `Scanner` and `Executor` for headless/batch runs.
- **Window geometry persistence.** Save size and position between launches.
- **Group by stem** (ignore extension) as an option, for `photo.jpg` + `photo.png` that belong together.
- **Content-hash duplicate detection** as an opt-in secondary pass, to catch `Img001.raw` / `Img001.png` / `Img001.jpg` that are the same underlying image.
- **Permanent-delete button for quarantine.** After review, allow the user to purge a specific quarantine folder with an explicit confirmation. Currently the user must delete it manually.
- **EXIF capture-time comparison** as an alternative sort key.
- **Packaged executable** for users who do not want to install Python.

Current starter limitations still on the table:

- No automatic rollback or one-click restore (see "Manifest-driven recovery" above).
- No permanent-delete button for quarantine.
- No duplicate detection across different filenames.
- No EXIF capture-time comparison.
- No HEIC preview (extensions can be added, but Pillow codec support varies).
- No packaged executable installer.

If you want to work on any of these, open an issue first to coordinate, then send a PR. Small, focused PRs are preferred over sweeping rewrites.

---

## Contributing

- Bug reports: include the OS, Python version, a minimal reproduction, and — if the failure was mid-execute — the manifest CSV.
- Feature requests: check [Status & Scope](#status--scope) first. If it's out of scope, a fork is the right answer, and that's fine.
- PRs: one concern per PR, with a note in the description about which behavior changed and why. If you touch `Executor`, add or extend a test.

---

## Acknowledgements

Built in an afternoon to solve a problem that had been sitting on an external drive for four years. Thanks to the `tkinter.ttk` maintainers for making a reviewable desktop UI achievable in a single file, and to whoever first wrote `shutil.move` — you saved me from writing cross-device file handling by hand.
```
