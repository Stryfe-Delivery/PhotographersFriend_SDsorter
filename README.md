# Photo Consolidator Rev2

> A bespoke desktop tool for flattening a chaotic photo tree into a single, deduplicated, conflict-reviewed folder — with staging, quarantine, and a full audit manifest.


[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#requirements)
[![Tkinter](https://img.shields.io/badge/UI-Tkinter-informational.svg)](https://docs.python.org/3/library/tkinter.html)
[![Status](https://img.shields.io/badge/status-bespoke%20%2F%20maintained-brightgreen.svg)](#status--scope)
[![PRs](https://img.shields.io/badge/PRs-welcome-success.svg)](#contributing)

---

## Why this exists

I shoot a lot, and I edit in rounds. Over a couple of years I ended up with the same shoot scattered across a laptop, an external drive, two SD cards, and three "final_final_v2" folders. Filenames like `IMG_0421.JPG` and `DSC_0421.NEF` and `0421-edit.jpg` collided constantly — sometimes they were genuinely different photos, sometimes they were the same photo at different stages.

I didn't want a photo library. I didn't want faces, albums, tags, or a database. I wanted to point a tool at one folder, let it walk the whole tree, and end up with:

- One copy of each filename at the top level.
- Every legitimate name collision surfaced and reviewed by me, not silently overwritten.
- Everything else moved to a quarantine folder I could triage later, in one place, with a CSV telling me exactly what happened.
- The ability to abort without losing data.

That's all. It's a single-task tool. It does one thing. If you need catalog management, use something else — see [Status & Scope](#status--scope).

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

- Python **3.9 or newer** (uses `from __future__ import annotations`, `zoneinfo`-free — should work on 3.8, untested).
- **Tkinter** — bundled with most CPython installs; on Debian/Ubuntu it's `python3-tk`.
- **Pillow** *(optional)* — only needed for thumbnail previews.

```bash
python -m pip install Pillow
```

Without Pillow, the preview pane shows the relative path and a hint instead of a thumbnail. Everything else works.

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

## Usage

1. **Select Folder** — choose the root you want to flatten. Everything under this folder will be considered.
2. **(Optional) File Types tab** — adjust which extensions are scanned. Raw formats are included by default but won't preview.
3. **Scan** — walks the tree. Nothing is changed. Conflicts and unique names appear in the left tree.
4. **Review** — click a group, pick the variant you want to keep. Use the filter, the conflicts-only checkbox, or the auto-select buttons to work faster.
5. **Execute Consolidation** — the confirmation dialog tells you exactly where the quarantine will be created. On confirm, the three-phase move runs with progress.
6. **Inspect** — after completion, use the Open Root / Open Quarantine / Open Manifest Folder buttons to verify.

### Aborting safely

- **Scan**: no files are touched. Abort just stops the walk.
- **Execute**: the current file move finishes, then the worker stops, writes a partial manifest, and returns. Staging and quarantine are left intact. No rollback is attempted — the manifest is your recovery recipe.

---

## Design notes

- **All UI on the Tk main thread.** Workers are pure filesystem/CPU classes that communicate via a `queue.Queue`. No Tk call ever crosses a thread boundary.
- **`os.replace` first, `shutil.move` second.** Same-filesystem moves are atomic renames. Cross-filesystem moves fall back to copy+delete. The tool warns you before starting if it detects `root` and `root.parent` on different devices.
- **No silent overwrites.** Every destination is checked before the move. If it exists, the operation raises rather than clobbering.
- **No permanent deletion anywhere.** Quarantine exists precisely so that a wrong decision is recoverable. The manifest is the ledger.
- **Self-exclusion.** Folders prefixed with `_photo_consolidator_` are skipped during scan, so re-running the tool on a parent directory doesn't re-ingest your own quarantine.

---

## Status & Scope

This is a **bespoke tool built for a single large consolidation task** — hundreds of gigabytes spread across multiple drives, multiple cameras, and multiple rounds of edits. It is intentionally not a photo manager.

**Intentionally missing** (do not open issues asking for these):

- No library, catalog, or database.
- No albums, tags, ratings, faces, or keywords.
- No cloud sync, upload, or sharing.
- No editing, RAW development, or export presets.
- No deduplication by content hash (grouping is by filename only).
- No "portfolio management" of any kind.

If you need any of the above, use Darktable, Lightroom, digiKam, PhotoPrism, or Immich. This tool exists for the flat, deterministic, reviewable job of *getting files out of a mess and into one folder without losing anything*.

It can absolutely be forked and extended for other one-off tasks — batch renames, format conversions, manifest-driven recovery — but the maintainer's scope is deliberately narrow.

---

## Potential next steps

These are the directions that would make the tool more useful beyond its original run. **Pull requests are welcome** for any of them.

- **Manifest-driven recovery.** Read a manifest CSV and reverse it: move KEEP files back to their original relative paths, move QUARANTINE files back from quarantine. The manifest already has everything needed; only the reverse executor is missing.
- **Dry-run mode.** Produce the manifest and plan without moving anything, for large runs you want to preview.
- **Unit tests.** The `Scanner` and `Executor` classes are pure and testable with `tmp_path`. Highest-value targets: grouping, newest-default selection, `safe_move` same-fs and cross-fs, abort-mid-phase manifest contents.
- **Logging to file.** Replace ad-hoc `status_var` updates with `logging` to a rotating file next to the manifest, so post-mortem inspection doesn't depend on a dismissed messagebox.
- **CLI mode.** An `argparse` entrypoint that reuses `Scanner` and `Executor` for headless/batch runs.
- **Window geometry persistence.** Save size and position between launches.
- **Group by stem** (ignore extension) as an option, for `photo.jpg` + `photo.png` that belong together.
- **Content-hash duplicate detection** as an opt-in secondary pass.

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
