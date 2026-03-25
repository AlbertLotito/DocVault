# Archive X-Ray — Design Spec

**Date:** 2026-03-24
**Status:** Approved for implementation

---

## Goal

Wire the existing Archive X-Ray extractor into the live system and add a rich browse experience to search results so users can see what is inside an archive without leaving the search page.

## Architecture

Two independent concerns:

**A — System kernel auto-certification (no new code needed)**
`core/registry.py` already contains `register_system_kernels()`, which is already called in `run.py` at startup. It performs a single `sync_disk_to_db()` followed by a bulk SQL UPDATE that certifies all kernels matching `com.docvault.*`, `com.microsoft.*`, `com.openai.*`, or `com.google.*` where `certified_at IS NULL` or `status = 'missing'`. The archive extractor's id `com.docvault.system.archive_xray` matches `com.docvault.%`. On first discovery the kernel is inserted with `certified_at = NULL`, so the next startup call to `register_system_kernels()` will certify it automatically. **No code changes required for A.**

**B — Archive browse experience**
The extractor currently owns its own ZIP/TAR/7Z/RAR readers and produces plain text for FTS5. The browse endpoint needs the same structured data, so the readers — along with the shared classification helpers (`_classify`, `_GROUP_ORDER`) — are extracted to `core/archive_reader.py`. Both the extractor and the new API endpoint import from there. The endpoint re-reads the archive on demand (the central directory read is fast on local disk and requires no DB schema changes). The UI detects archive file types in search results, shows a compact metadata line, and expands a collapsible browse panel on demand.

---

## File Map

| File | Change |
|---|---|
| `core/archive_reader.py` | **New.** `read_entries()`, `_classify()`, `_GROUP_ORDER` — shared archive logic |
| `extractors/archive_xray_extractor.py` | Import from `core.archive_reader`; remove duplicate functions |
| `api/routes/catalog.py` | New `GET /catalog/archive` endpoint (registered before `/{file_hash}`) |
| `frontend/search.html` | Archive badge, meta line, Browse toggle, inline panel |
| `tests/test_archive_reader.py` | **New.** Unit tests for `core/archive_reader.py` |
| `tests/test_archive_xray.py` | Update imports; `extract()` behaviour tests unchanged |

---

## core/archive_reader.py

### Public API

```python
def read_entries(file_path: str) -> tuple[list[dict], int, str]:
    """Read archive central directory without extracting content.

    Returns (entries, total_compressed_bytes, format_name).

    Each entry dict: {name: str, size: int, mtime: str, is_dir: bool, encrypted: bool}
    - size: uncompressed size in bytes (0 for directories)
    - mtime: ISO date string 'YYYY-MM-DD', or '' if unavailable
    - is_dir: True for directory entries
    - encrypted: True if this entry is encrypted (per-entry flag, ZIP only)

    format_name values: 'ZIP', 'TAR', 'TAR.GZ', 'TAR.BZ2', 'TAR.XZ', '7Z', 'RAR'

    Password handling differs by format:
    - ZIP: no pre-read password check is possible; entries are always returned with
      `encrypted: True` for entries that are encrypted. PermissionError is NOT raised for ZIP.
    - 7Z: raises PermissionError('Password protected') if the archive requires a password.
    - RAR: raises PermissionError('Password protected') if the archive requires a password.
    - TAR: no encryption support; encrypted field is always False.

    Other raises:
        ImportError      — py7zr (for .7z) or rarfile (for .rar) not installed; message includes install hint
        tarfile.TarError — standalone .gz/.bz2 that is not a tar archive
        OSError          — file not found, unreadable, or corrupt
    """
```

### Shared classification helpers (also in this module)

```python
_GROUP_ORDER: list[str]  # ordered list of group names, same as existing extractor

def _classify(filename: str) -> str:
    """Return the group name for a filename based on its extension."""
```

These are moved verbatim from `archive_xray_extractor.py`. The extractor imports them from here.

---

## extractors/archive_xray_extractor.py

Replace the four private reader functions (`_read_zip`, `_read_tar`, `_read_7z`, `_read_rar`) and the `_GROUPS`, `_GROUP_ORDER`, `_classify` definitions with imports:

```python
from core.archive_reader import read_entries, _classify, _GROUP_ORDER
```

`extract()` calls `read_entries(file_path)` and passes the result to `_build_output()`. The existing `except tarfile.TarError` handler in `extract()` (which returns `(None, "Standalone compressed file — not a tar archive", None)`) is retained unchanged — `read_entries()` propagates this exception for standalone compressed files and `extract()` must catch it. All other logic (`_build_output`, `_fmt_size`, `_fmt_date`, `_is_top_readme`, MANIFEST) stays in the extractor file unchanged.

---

## API — GET /catalog/archive

Added to `api/routes/catalog.py`.

**Route ordering:** This route must be registered before the existing `@router.get("/catalog/{file_hash}")` parameterised route. It should follow immediately after `GET /catalog/inspect`, which is already before `/{file_hash}`.

**Request:** `GET /catalog/archive?path=<url-encoded file path>`

**Behaviour:**
1. Look up `path` using `WHERE file_path = ?` in the `tasks` table (same column and pattern as the existing `/catalog/inspect` endpoint). Return 404 if not found.
2. Validate `file_type` is in the known archive extension set: `{zip, tar, tgz, tbz2, gz, bz2, 7z, rar}`.
   - Note: `file_type` is stored as the last extension only (from `os.path.splitext()`), so `.tar.gz` files have `file_type = 'gz'` and `.tar.bz2` files have `file_type = 'bz2'`. These are both in the set. Return 400 if `file_type` is not in the set.
3. Call `core.archive_reader.read_entries(file_path)`.
   - Catch `PermissionError` → return 200 with `password_protected: true`, empty `entries` and `groups`. This applies only to 7Z and RAR archives.
   - Catch all other exceptions → return 500.
4. Compute `groups` using `_classify` from `core.archive_reader`. Only include groups with at least one non-directory entry. Order by `_GROUP_ORDER`.
5. Return the response.

**Success response (200):**

```json
{
  "format": "ZIP",
  "file_count": 42,
  "total_uncompressed_bytes": 15204352,
  "total_compressed_bytes": 5021234,
  "password_protected": false,
  "groups": [
    {"name": "Source Code", "count": 35},
    {"name": "Documents",   "count": 7}
  ],
  "entries": [
    {"name": "README.md", "size": 1024, "mtime": "2024-01-15", "is_dir": false, "encrypted": false}
  ]
}
```

- `file_count`: total number of non-directory entries in the archive. May exceed `len(entries)` if capped.
- `total_uncompressed_bytes`: sum of `entry['size']` for all non-directory entries.
- `total_compressed_bytes`: the second element returned by `read_entries()`. For TAR formats this is the file's on-disk size (TAR has no per-entry compressed size); for ZIP/7Z/RAR this is the sum of per-entry compressed sizes.
- `entries`: up to 500 non-directory entries. The frontend renders all 500 and shows "N more files not shown" where N = `file_count - 500` if `file_count > 500`.
- `password_protected: false` in a 200 response means the listing was successfully read.

**Password-protected response (200, 7Z and RAR only):**

```json
{
  "format": "7Z",
  "file_count": 0,
  "total_uncompressed_bytes": 0,
  "total_compressed_bytes": 0,
  "password_protected": true,
  "groups": [],
  "entries": []
}
```

For password-protected ZIP archives, the entries are returned normally with `encrypted: true` on each entry — a 200 response with populated entries, not the password-protected shape above.

**Error responses:**
- `404` — path not found in `tasks` table (by `file_path`)
- `400` — `file_type` is not a recognised archive extension
- `500` — archive is unreadable, corrupt, or required optional library (`py7zr`, `rarfile`) not installed

---

## Frontend — search.html

### Archive detection

Content search results (`renderResults`) return only FTS chunk fields (`file_hash`, `file_path`, `chunk_text`, `snippet`, `rank`). They do NOT include `file_type` or `metadata_json`. Archive detection in content search must therefore derive the type from `file_path` extension:

```js
const ARCHIVE_TYPES = new Set(['zip','tar','tgz','tbz2','gz','bz2','7z','rar']);

function isArchivePath(filePath) {
  const ext = (filePath || '').split('.').pop().toLowerCase();
  return ARCHIVE_TYPES.has(ext);
}
```

Filename search results (`renderFilenameResults`) return the full task record including `file_type` and `metadata_json`, so either field may be used for detection there.

### formatSize() extension

The existing `formatSize()` helper in `search.html` only handles up to MB. It must be extended to handle GB and TB before the archive panel can display large entry sizes correctly:

```js
function formatSize(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
```

This replaces the existing `formatSize` definition in-place. TB is omitted — a single archive entry is unlikely to exceed 1 TB.

### Changes to renderResults() (content search)

For archive results (detected via `isArchivePath(r.file_path)`):
- Add `[ARCHIVE]` badge (amber `lc-badge`) next to the filename.
- **Add** a `[Browse ▶]` button in the action row alongside the existing `[Open]`, `[Folder]`, and `[Inspect]` buttons.
- No compact meta line in content search results — `metadata_json` is not available in FTS result rows.

### Changes to renderFilenameResults() (filename search)

For archive results (detected via `file_type` or `isArchivePath`):
- Add `[ARCHIVE]` badge (amber `lc-badge`) next to the filename.
- Add `[Browse ▶]` button alongside the existing `[Open]`, `[Folder]`, and `[Inspect]` buttons.
- Show compact meta line: `ZIP · 42 files · 15.2 MB` below the filename. Populated from `metadata_json` fields `format`, `file_count`, and `total_size_bytes`. Note: the DB field is named `total_size_bytes` (set by the extractor), while the API response field is named `total_uncompressed_bytes` — both represent the same uncompressed total. Use the updated `formatSize()` helper. Hide the meta line if `metadata_json` is absent, null, or unparseable.

### browseArchive(path, btnEl)

Async function called when Browse is clicked:

1. Toggle: if the panel for this path is already open, close it, restore button text to `Browse ▶`, return.
2. Change button text to `Browse ▼`. Insert a `div.archive-browse-panel` immediately after the result card.
3. Show a loading spinner inside the panel.
4. Fetch `GET /api/catalog/archive?path=<path>`.
5. On success:
   - If `password_protected: true`: render "Password protected — file listing unavailable."
   - Otherwise: render panel header, group pills, then the file listing (all entries from response), then "N more files not shown" if `file_count > 500`.
6. On HTTP error or network failure: render "Could not read archive."

### Archive browse panel layout

```
┌─────────────────────────────────────────────────────────────┐
│ ZIP · 42 files · 15.2 MB uncompressed · 67% compression     │
│                                                              │
│ [Source Code 35]  [Documents 7]                              │
│                                                              │
│ README.md                        1.0 KB    2024-01-15        │
│ src/main.py                     24.5 KB    2024-01-10        │
│ ...                                                          │
│ [ 958 more files not shown ]                                 │
└─────────────────────────────────────────────────────────────┘
```

- Header row: format, file count, uncompressed size (from `total_uncompressed_bytes`), compression ratio computed client-side as `Math.round((1 - total_compressed_bytes / total_uncompressed_bytes) * 100)`. Omit ratio if `total_uncompressed_bytes === 0`.
- Group pills: one pill per group present, showing group name and count, inline flex row.
- File listing: monospace rows — name (truncated to 60 chars; `title` attribute shows full path), right-aligned size via `formatSize()`, date. `[encrypted]` tag appended for entries with `encrypted: true`. Directory entries are not shown.
- All entries from the API response are rendered (up to 500). "N more files not shown" line if `file_count > 500`, where N = `file_count - 500`.

### New CSS classes

```css
.archive-badge         /* lc-badge variant, amber, matches --c-warn */
.archive-browse-panel  /* dark inset panel below result card */
.archive-group-pills   /* inline flex row */
.archive-group-pill    /* individual group pill */
.archive-entry-row     /* single file row, monospace */
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Archive file deleted after indexing | Endpoint returns 500; UI shows "Could not read archive" |
| 7Z or RAR is password-protected | Endpoint returns 200 with `password_protected: true`; UI shows "Password protected — file listing unavailable" |
| ZIP is fully encrypted | Endpoint returns 200 with entries, all having `encrypted: true`; panel renders with `[encrypted]` markers |
| py7zr / rarfile not installed | Endpoint returns 500; server log contains install hint; UI shows generic error |
| Archive not yet extracted (no `metadata_json`) | Meta line hidden in UI; Browse still works (reads live from disk) |
| File not in `tasks` table | 404 |
| `file_type` not a recognised archive extension | 400 |

---

## Testing

### tests/test_archive_reader.py (new)

- `read_entries` on an in-memory ZIP returns correct entry dicts (name, size, mtime, is_dir=False for files, encrypted=False)
- `read_entries` on a ZIP with a single encrypted entry returns `encrypted: True` for that entry; does NOT raise PermissionError
- `read_entries` on a TAR returns entries with correct `is_dir` flags and format string `'TAR'`
- `read_entries` on a TAR.GZ returns format string `'TAR.GZ'`
- `read_entries` on a standalone `.gz` (not a tarball) raises `tarfile.TarError`
- `read_entries` on a non-existent path raises `OSError`
- `read_entries` on a `.7z` raises `ImportError` when `py7zr` is patched as unimportable
- `_classify` returns expected group names for known extensions (py → 'Source Code', pdf → 'Documents', jpg → 'Images')
- `_classify` returns 'Other' for unknown extensions

### tests/test_archive_xray.py (update)

- All 20 existing tests continue to pass unchanged — `extract()` behaviour is identical.
- `_classify` is now imported from `core.archive_reader`; tests that exercise it via `mod._classify(...)` continue to work because the extractor re-exports it.
- `_fmt_size` and `_is_top_readme` remain in `archive_xray_extractor.py` and are tested in place without import-location changes.

### tests/test_registry.py (extend or add)

- `register_system_kernels()` certifies a `com.docvault.system.*` kernel that has `certified_at = NULL`
- `register_system_kernels()` is idempotent — calling it twice does not update `certified_at` on an already-certified kernel, because the `WHERE certified_at IS NULL` clause excludes it from the second UPDATE
- `register_system_kernels()` does not certify a kernel with a non-matching namespace (e.g. `com.thirdparty.foo`)

---

## Out of Scope

- Deep extraction (indexing files inside archives as child tasks) — separate feature.
- Archive type filter in the search filter row — users can type `zip` in the existing file type filter.
- Streaming large listings — 500-entry server cap makes this unnecessary.
