# PDF Image Extraction Overhaul — Design Spec
**Date:** 2026-03-15
**Status:** Approved (v2 — post spec-review)

---

## Problem

The PDF Image Harvester (`image_extractor.py`) runs an Ollama vision inference call for
every embedded image in every PDF. On a 300-page scanned PDF this produces hundreds of
sequential LLM calls, making it the single largest throughput bottleneck in the pipeline.
Five concrete issues:

1. Repeated images (logos, watermarks, headers) are described once per occurrence.
2. No upper bound on images processed per file — a single PDF can monopolise the worker.
3. Tiny decorative images (icons, bullets, dividers) trigger full LLM inference.
4. Scanned PDFs: the text extractor already OCR'd/vision-analysed each page; the image
   extractor then re-analyses the same pixels a second time.
5. Vision inference blocks extraction completion — the PDF task stays EXTRACTING until
   every Ollama call returns.

---

## Goals

- Eliminate redundant vision calls.
- Make PDF extraction fast regardless of image count.
- Preserve full search fidelity: image descriptions searchable both as part of the parent
  PDF and as independent records.
- Preserve parent→child relationship for context-aware search.

---

## Architecture Overview

### Parent–Child Task Model

The PDF Image Harvester becomes a **fast pre-processor**: it filters, saves PNGs, and
registers each qualifying image as a child `PENDING` task. No Ollama calls happen inline.

The existing extraction worker picks up each PNG child task and runs
`intelligent_image_extractor` on it (vision + OCR fallback) as normal.

When a child task completes, the extraction worker performs a **write-back**: it appends
the image description to the parent PDF's `extracted_text`, updates FTS, and re-queues
the parent for re-embedding if it was already `COMPLETED`.

This gives us:
- **Fast PDF extraction** — harvester returns in milliseconds.
- **Independent image records** — each PNG is its own searchable entity with Qdrant vector.
- **Enriched PDF record** — image descriptions flow back into the parent's FTS + semantic
  index once child tasks complete.

---

## Data Model Changes

### `tasks` table — new column

```sql
ALTER TABLE tasks ADD COLUMN parent_hash TEXT REFERENCES tasks(file_hash);
CREATE INDEX IF NOT EXISTS idx_tasks_parent_hash ON tasks(parent_hash);
```

Migration runs on startup via `init_db()` using the standard `PRAGMA table_info` guard
(same pattern as the `vault_id` migration). This is a **mandatory** change — without it
neither `insert_task()` nor the write-back can function.

### `ChildTask` dataclass (`core/extractors/base.py`)

Two new fields added — **mandatory**:

```python
@dataclass
class ChildTask:
    file_path:     str
    file_type:     str
    vault_id:      str
    file_hash:     str = ''                            # NEW — SHA-256 of saved PNG
    priority:      int  = 10
    parent_hash:   str | None = None                   # NEW
    metadata_json: dict = field(default_factory=dict)  # NEW
```

### Child task `metadata_json` payload

Each child task carries context for write-back and search enrichment:

```json
{
  "parent_hash":  "sha256...",
  "parent_path":  "E:/Docs/report.pdf",
  "page_num":     3,
  "image_index":  2
}
```

Qdrant payload for the PNG vector inherits these fields, enabling search results to
surface *"page 3 of report.pdf"* for any image hit.

---

## New Settings

| Key | Default | Description |
|-----|---------|-------------|
| `pdf:min_image_area` | `10000` | Minimum pixel area (w×h). Images below this are skipped. |
| `pdf:max_images_per_pdf` | `50` | Maximum child tasks spawned per PDF. `0` = no cap. Negative values treated as 0. |

Both added to `Settings.schema` in `core/settings.py` under the `pdf` group.

---

## Image Filter Pipeline (Combined Pseudocode)

Filters apply in this exact order inside `image_extractor.extract()`. Only images that
pass all three are saved as PNGs and spawned as child tasks.

```
seen_hashes = set()
spawned     = 0
max_images  = max(0, int(settings.get('pdf:max_images_per_pdf') or 50))  # negative → 0 (no cap)
min_area    = int(settings.get('pdf:min_image_area') or 10000)

for each page in pdf:
    for each image on page:

        # 1. Dedup (opt 1)
        img_hash = sha256(pil_img.tobytes())
        if img_hash in seen_hashes → skip, log "duplicate"
        seen_hashes.add(img_hash)

        # 2. Area filter (opt 3)
        if width * height < min_area → skip, log "too small"

        # 3. Cap (opt 2)  — only qualifying images count toward cap
        if max_images > 0 and spawned >= max_images → stop all iteration, log "cap reached"

        # 4. OCR-page skip (opt 4)
        if page_num in ocr_pages AND (width * height) >= 0.8 * page_area → skip, log "full-page already OCR'd"

        # 5. Save PNG + spawn child task
        save PNG to cache
        append ChildTask to result.child_tasks
        spawned += 1
```

---

## Optimization 1 — Hash-Based Deduplication

SHA-256 of `pil_img.tobytes()` per image. Per-PDF `seen_hashes` set. Eliminates
logo/watermark/header repetitions — typically 60–90% of images in corporate documents.

---

## Optimization 2 — Per-PDF Cap

Counter of spawned child tasks. When `spawned >= max_images` (and `max_images > 0`),
stop iterating pages. Log `"cap reached at N: skipped M images"`.

`max_images = 0` disables the cap entirely (explicit zero-check required in code:
`if max_images > 0 and spawned >= max_images`).

---

## Optimization 3 — Area Filter

`width * height < min_area` → skip. Replaces the current 1D `_MIN_DIMENSION` check in
`vision.py` for this path. The `vision.py` check remains as a backstop for standalone
images processed by other paths.

---

## Optimization 4 — Skip OCR'd Pages

**Part A — `text_extractor.py`:**

After processing each page, if Tesseract or Vision LLM was used (native text was sparse),
add the page number to `ocr_pages`. Return as a 3-tuple:

```python
return text, None, {'ocr_pages': list(ocr_pages)}
```

The `LegacyExtractorAdapter.normalize()` in `base.py` already handles 3-tuples
(lines 288–291) — no adapter changes needed. The third element is stored in
`IngestResult.metadata`.

**Part B — Mid-flight metadata write in `extraction_worker.py`:**

The extraction worker iterates extractors in sequence. After the text extractor completes
and before the image extractor runs, write the metadata to the DB:

```python
manager.update_task_metadata(db_path, file_hash, result.metadata)
```

`update_task_metadata()` merges the given dict into the task's existing `metadata_json`
(JSON merge, not overwrite). Signature:

```python
def update_task_metadata(db_path: str, file_hash: str, metadata: dict) -> None
```

The image extractor detects it is running on a PDF by reading `metadata_json` from the
task record at the start of `extract()`. It parses `ocr_pages` from there.

**Part C — image_extractor.py:**

At start of `extract()`, read task's current `metadata_json` via
`manager.get_task_metadata(db_path, file_hash)` (new helper, returns dict or `{}`).

Parse `ocr_pages = set(metadata.get('ocr_pages', []))`.

In the filter pipeline: if `page_num in ocr_pages` AND the image is full-page
(`width * height >= 0.8 * page_width * page_height`), skip. pypdf page dimensions
available via `page.mediabox`.

---

## Optimization 5 — Deferred Vision via Child Tasks

### Part A — Harvester is now a scheduler

`image_extractor.extract()` applies all filters (opts 1–4), saves qualifying PNGs,
populates `result.child_tasks` with one `ChildTask` per image. **No `_analyze_image()`
call. No Ollama.** Returns immediately after iterating pages.

### Part B — Child task dispatch in `extraction_worker.py` (NEW — does not exist yet)

After calling `extractor.run()` and before calling `complete_extraction()`, the worker
must loop over `result.child_tasks` and insert each:

```python
for ct in result.child_tasks:
    manager.insert_task(
        db_path, ct.file_hash, ct.file_path, ct.file_type,
        priority=ct.priority, vault_id=ct.vault_id,
        parent_hash=ct.parent_hash,
        metadata_json=ct.metadata_json,
    )
```

`manager.insert_task()` must be extended with `parent_hash=None` and
`metadata_json=None` parameters — these are **mandatory** additions, not conditional.
Uses `INSERT OR IGNORE` (idempotent on re-run).

`ct.file_hash` is computed by the harvester: `hashlib.sha256(open(out_path,'rb').read()).hexdigest()`.

### Part C — Write-back in `extraction_worker.py`

After a task completes whose `parent_hash` is not None:

1. Read `extracted_text` from result and build suffix:
   ```
   [Image, Page {page_num}, #{image_index} ({child_hash[:8]}): {description}]
   ```
   The `child_hash[:8]` prefix is the **idempotency guard** — see below.

2. Call `manager.append_parent_text(db_path, parent_hash, suffix)`:
   ```python
   def append_parent_text(db_path: str, parent_hash: str, suffix: str) -> None:
       """Append suffix to parent's extracted_text, update FTS, idempotent."""
       with _connect(db_path) as conn:
           row = conn.execute(
               "SELECT extracted_text FROM tasks WHERE file_hash=?", (parent_hash,)
           ).fetchone()
           if not row:
               return
           existing = row['extracted_text'] or ''
           # Idempotency guard: skip if this child's hash prefix already in text
           guard = suffix.split(')')[0] + ')'   # matches "[Image, Page N, #M (abcd1234)]"
           if guard in existing:
               return
           new_text = existing + '\n\n' + suffix
           conn.execute(
               "UPDATE tasks SET extracted_text=? WHERE file_hash=?",
               (new_text, parent_hash)
           )
           conn.commit()
       manager.update_fts(db_path, parent_hash)
   ```

3. Reset parent for re-embedding — **atomic** to avoid race condition with concurrent
   child completions:
   ```python
   manager.reset_to_extracted_if_complete(db_path, parent_hash)
   ```
   ```python
   def reset_to_extracted_if_complete(db_path: str, file_hash: str) -> None:
       with _connect(db_path) as conn:
           conn.execute(
               "UPDATE tasks SET status='EXTRACTED' WHERE file_hash=? AND status='COMPLETED'",
               (file_hash,)
           )
           conn.commit()
   ```
   The `AND status='COMPLETED'` condition makes this atomic — concurrent write-backs
   both succeed but only the first changes status; subsequent ones are no-ops.

### `update_fts()` helper

Must be extracted from the inline FTS logic in `complete_extraction()` so both code
paths share it:

```python
def update_fts(db_path: str, file_hash: str) -> None:
    """Re-sync FTS index for a single task from its current extracted_text."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path, extracted_text FROM tasks WHERE file_hash=?",
            (file_hash,)
        ).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM fts_index WHERE file_hash=?", (file_hash,))
        if row['extracted_text']:
            conn.execute(
                "INSERT INTO fts_index(file_hash, file_path, content) VALUES (?,?,?)",
                (file_hash, row['file_path'], row['extracted_text'])
            )
        conn.commit()
```

---

## Files Changed

| File | Change |
|------|--------|
| `core/extractors/base.py` | `ChildTask`: add `parent_hash: str \| None = None` and `metadata_json: dict` |
| `core/manager.py` | `init_db()`: parent_hash column + index migration; extend `insert_task()` with `parent_hash`, `metadata_json`; add `append_parent_text()`, `update_fts()`, `update_task_metadata()`, `get_task_metadata()`, `reset_to_extracted_if_complete()` helpers |
| `core/settings.py` | Add `pdf:min_image_area`, `pdf:max_images_per_pdf` to schema |
| `extractors/text_extractor.py` | Track `ocr_pages` per page; return 3-tuple with metadata |
| `extractors/image_extractor.py` | Full rewrite: apply filter pipeline (dedup → area → cap → OCR-skip); spawn `ChildTask` per image; no inline vision calls |
| `workers/extraction_worker.py` | (1) Mid-flight `update_task_metadata()` after text extractor; (2) child task dispatch loop; (3) write-back on child completion |

---

## Out of Scope

- UI for viewing image→PDF relationships (future: catalog detail page)
- Re-embedding trigger UI (handled automatically by write-back)
- Settings UI for new pdf:* keys (accessible via Settings page — they'll appear under the pdf group automatically)
