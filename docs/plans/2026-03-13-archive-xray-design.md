# Archive X-Ray — Design Document
_2026-03-13_

## Goal

Index the contents of archive files (zip, tar, 7z, rar) without extracting them to disk. Produces a structured text summary that is fully searchable via FTS and semantic search.

---

## Architecture

Single new kernel: `extractors/archive_xray_extractor.py`

- Follows standard kernel pattern: `MANIFEST` dict, lazy imports via `_ensure_loaded()`, `extract(file_path, ctx) → (result, err)` signature.
- Registered as `com.docvault.system.archive_xray` — auto-certified on startup alongside other system kernels.
- No changes to router, ingestor, or manager required.

**Extensions covered:** `.zip` `.tar` `.tar.gz` `.tar.bz2` `.tar.xz` `.tgz` `.tbz2` `.7z` `.rar`

**New dependencies:**
- `py7zr` — pure Python 7z support (pip install)
- `rarfile` — RAR support (pip install; requires `unrar` binary on PATH for RAR v5+)

---

## Output Format

```
Archive: project-backup.zip
Format: ZIP  |  Files: 847  |  Uncompressed: 2.31 GB  |  Ratio: 68%

Contents by Type:
  Source Code    201 files   (py: 145, js: 34, ts: 22)
  Documents       34 files   (pdf: 20, docx: 14)
  Images          12 files   (jpg: 8, png: 4)
  Data             8 files   (json: 5, csv: 3)
  Audio            1 file    (mp3: 1)
  Other            4 files

File Listing:
  README.md                         3.1 KB   2024-01-15
  src/main.py                      45.2 KB   2024-01-15
  docs/architecture.pdf           892.0 KB   2024-01-10
  ...
```

---

## File Listing Sort Order

1. Top-level README files first (filename only matches `readme.*`, case-insensitive — no path separator)
2. Remaining files by size descending

---

## File Type Groups

| Group | Extensions |
|---|---|
| Source Code | py js ts java c cpp h cs go rb php swift kt rs sh bat |
| Documents | pdf doc docx xls xlsx ppt pptx odt ods odp rtf |
| Images | jpg jpeg png gif bmp tiff webp svg ico raw cr2 nef arw |
| Audio | mp3 wav flac ogg m4a aac opus |
| Video | mp4 mkv avi mov wmv webm flv |
| Archives | zip 7z rar tar gz bz2 |
| Data | json xml csv yaml toml sql db sqlite |
| Other | everything else |

---

## Large Archive Handling

Archives with more than 500 files: listing capped at 500 entries (after README priority + size sort), with a trailing note:
```
[ 347 more files not shown ]
```

---

## Password-Protected Archives

| Format | Behaviour |
|---|---|
| ZIP | Central directory is always readable; encrypted entries flagged `[encrypted]` |
| 7z / RAR | Catch exception; show header block, replace listing with `Password protected — file listing unavailable` |

---

## Error Handling

- Missing `py7zr` or `rarfile`: catch `ImportError`, return `(None, "py7zr not installed")` etc.
- Missing `unrar` binary: `rarfile` raises `rarfile.RarCannotExec`; catch and return graceful error.
- Corrupt archive: catch all format-specific exceptions + broad `Exception`; return `(None, err_str)`.

---

## What Is NOT in Scope

- Extracting file contents (Option B)
- Nested archive recursion
- Password prompting
- Standalone `.gz` / `.bz2` files (single-file compression, no listing to show)
