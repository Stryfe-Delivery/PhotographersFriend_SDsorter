# Photo Consolidator

Photo Consolidator is a local Python/Tkinter desktop utility for consolidating photos from a folder tree into the selected folder's top level. This allows users to merge backups that contain duplicates safely while also managing duplicate filename or size issues from swapping sd cards, resetting cameras etc. In a classic windows environment in many case the file comparison info is not shown or it is too cumbersome to review each file. 

The utility recursively finds supported image files, groups files with clashing names, preselects the newest variant, and lets the user choose the single version to retain. Retained images are moved through a verified staging area, while all other folder content is moved to a timestamped quarantine folder rather than permanently deleted. The quarantine can be offloaded onto a spare drive or deleted once the selected versions are imported into your normal processing pipeline. 

## Key Features

- Recursively scans a selected root folder
- Recognizes `.png`, `.jpg`, and `.jpeg` files, case-insensitively (user can modify in source)
- Groups filename conflicts case-insensitively
- Shows all variants associated with a conflicting filename
- Preselects the newest variant in each group
- Allows the user to retain a different variant
- Displays image previews when Pillow is installed
- Uses SHA-256 hashes to verify retained images after staging
- Moves non-retained content to a sibling quarantine folder
- Writes a timestamped CSV operation manifest
- Does not permanently delete files
- Does not follow symbolic links during scanning

## Installation Prerequisites

### Required

1. **Python 3.9 or newer**

   Check the installed Python version:

   ```bash
   python --version
   ```

   On systems where `python` points to an older version or is not available, try:

   ```bash
   python3 --version
   ```

2. **Tkinter**

   Tkinter is part of the Python standard library, but some Linux distributions install it separately.

   Test whether Tkinter is available:

   ```bash
   python -m tkinter
   ```

   A small Tk window should open. If it does, Tkinter is installed correctly.

   Common Linux installation commands are:

   **Ubuntu or Debian:**

   ```bash
   sudo apt install python3-tk
   ```

   **Fedora:**

   ```bash
   sudo dnf install python3-tkinter
   ```

   **Arch Linux:**

   ```bash
   sudo pacman -S tk
   ```

   Standard Python installers for Windows and macOS normally include Tkinter.

### Recommended

3. **Pillow**

   Pillow provides JPG and PNG thumbnail previews in the conflict-review interface.

   Install it with:

   ```bash
   python -m pip install Pillow
   ```

   The application runs without Pillow, but image previews are disabled.

### No Additional Runtime Packages Required

All other modules used by the application are from the Python standard library:

- `csv`
- `datetime`
- `hashlib`
- `os`
- `pathlib`
- `shutil`
- `tkinter`

## Files

```text
photo-consolidator/
├── photo_consolidator.py
└── README.md
```

## Running the Application

Open a terminal in the project folder and run:

```bash
python photo_consolidator.py
```

If required on your system, use:

```bash
python3 photo_consolidator.py
```

## Recommended First Run

Test the application on a copied sample folder before using it on the full photo collection.

A useful test structure is:

```text
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

This example creates a conflict group for `photo01.jpg`. The newest version is selected by default, but any displayed variant can be selected for retention.

## Usage

1. Start `photo_consolidator.py`.
2. Select **Select Folder**.
3. Choose the root folder to consolidate.
4. Select **Scan**.
5. Review filename groups marked **Conflict**.
6. Highlight a variant to view its metadata and preview.
7. Select **Retain Highlighted Variant** or double-click the desired variant.
8. Review the scan summary.
9. Select **Execute Consolidation**.
10. Confirm the staging and quarantine operation.

Scanning does not modify any files. Files are changed only after **Execute Consolidation** is selected and the confirmation dialog is accepted.

## Conflict Selection Logic

Filename groups are matched case-insensitively. For example, these names are treated as one conflict group:

```text
Photo01.JPG
photo01.jpg
PHOTO01.jpg
```

The default retained version is determined by sorting variants using:

1. Newest modification timestamp
2. Largest file size
3. Relative path as a deterministic tie-breaker

The user may override the default before execution.

## Two-Phase Quarantine Operation

The application intentionally avoids immediate permanent deletion.

### Phase 1: Stage and Verify Retained Images

- Each selected image is moved to a timestamped sibling staging folder.
- A SHA-256 hash is calculated before the move.
- The staged file is hashed again.
- Processing stops if the hashes do not match.

Example staging folder:

```text
_photo_consolidator_staging_Photos_20260916_145900
```

### Phase 2: Quarantine and Promote

- All content remaining inside the selected root is moved to a timestamped sibling quarantine folder.
- The staged retained images are moved into the root folder.
- The empty staging folder is removed.
- The quarantine folder is retained for manual review or deletion.

Example quarantine folder:

```text
_photo_consolidator_quarantine_Photos_20260916_145900
```

The staging and quarantine folders are created beside the selected root, not inside it. Ensure that the root folder's parent location has enough free space and that you have write permission there.

## Operation Manifest

A CSV manifest is written beside the selected root folder.

Example:

```text
_photo_consolidator_manifest_Photos_20260916_145900.csv
```

Manifest actions include:

- `KEEP`: selected image moved to staging
- `QUARANTINE`: remaining root entry moved to quarantine
- `PROMOTE`: staged image moved into the root
- `ERROR`: operation stopped because an error occurred

The manifest records source paths, destination paths, and SHA-256 hashes where applicable.

## Safety Notes

- The application changes the selected folder only after explicit confirmation.
- It does not permanently delete the quarantine folder.
- It does not automatically undo a partially completed operation.
- If processing stops, preserve the staging folder, quarantine folder, and manifest until the file locations have been reviewed.
- Do not select a folder currently being synchronized, indexed, backed up, or modified by another application.
- Close applications that may have the photos open before execution.
- Confirm that adequate free disk space is available in the selected root's parent location.
- Maintain an independent backup for valuable or irreplaceable images.

## Supported Files

The current starter implementation retains:

```text
.png
.jpg
.jpeg
```

All other files and directories are moved to quarantine during execution.

The application does not currently inspect image contents to determine whether a file extension is accurate.

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

### No thumbnail appears

Install Pillow:

```bash
python -m pip install Pillow
```

Restart the application afterward. 

### Permission denied

Confirm that your user account has permission to:

- Read every file under the selected root
- Create folders beside the selected root
- Move files out of and into the selected root
- Write the CSV manifest in the root's parent folder

Large raw files or files on local NAS/cloud drives may take longer to process, test using locally stored jpg and scale up from there.

### Files are on different filesystems or storage devices

The utility uses `shutil.move`. A move on the same filesystem is generally a rename operation. A move across filesystems may require copying and then removing the source. Avoid disconnecting storage or interrupting the application during execution.

### The operation stops partway through

Do not delete or rename the generated staging, quarantine, or manifest items. Review the manifest first. It records the completed operations and the reported error.

## Current Starter Limitations (potential next steps)

- No automatic rollback or one-click restore
- No permanent-delete button for quarantine
- No duplicate detection across different filenames e.g. Img001.raw Img001.png Img001.jpg are all seen as unique 
- No EXIF capture-time comparison
- No HEIC, TIFF, GIF, or RAW image support natively
- No background worker for very large scans or hash operations
- No packaged executable installer
