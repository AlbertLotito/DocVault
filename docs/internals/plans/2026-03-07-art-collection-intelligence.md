# Art Collection Intelligence — Design Plan

**Date:** 2026-03-07
**Status:** Planned — not yet implemented

## Overview

A three-tier system for indexing, describing, and enriching a large art image collection.
The collection lives at `Z:\Arts\collections\` and contains ~90 folders of varying structure.
Each folder = one independent collection, identified solely by its folder name.

## Data Model Assumptions

- Folder name = collection identifier (no parent/grandparent hierarchy inferred)
- Folder structure is irregular — some flat (images directly inside), some have artist subfolders
- Image files have no EXIF data and are often named numerically (e.g. `0045.jpg`)
- File types are mixed and come from unknown sources (JPG, PNG, BMP, TIFF, and others)
- `target_type = 'folder'` — extractor receives a folder path, not a file

---

## Tier 1 — Collection Indexer

**Speed:** Fast. Runs in normal extraction worker queue.
**AI:** None.

### What it does
- Receives a folder path
- Uses the folder name as the collection name
- Scans immediate folder contents for image files (non-recursive)
- Produces a structural summary: name, image count, formats present, file list
- Queues each image file as a child task tagged for Tier 2 enrichment

### Output (extracted text)
```
[ ART COLLECTION ]
Collection: Impressionism Movement of the World - 1860-1920
Images: 47
Formats: jpg (44), png (3)

[ FILE LIST ]
0001.jpg
0002.jpg
...
```

### Metadata
```json
{
  "collection": "Impressionism Movement of the World - 1860-1920",
  "image_count": 47,
  "formats": {"jpg": 44, "png": 3}
}
```

---

## Tier 2 — Vision Scanner

**Speed:** Slow. Idle-only worker thread.
**AI:** Ollama vision (local).

### What it does
- Picks up child tasks queued by Tier 1
- Runs `intelligent_image_extractor` (or direct Ollama vision) on each image
- Generates a natural-language description of what the painting looks like
- Updates the extracted text record in the DB

### Priority
Lower than normal extraction. Only runs when resource governor reports IDLE state.
Uses the existing `should_pause_or_throttle()` check in the worker loop.

---

## Tier 3 — Art Identifier + File Enrichment

**Speed:** Very slow. Idle-only, rate-limited.
**AI:** Google Vision API (remote).
**Side effects:** Writes `.nfo` sidecar files, renames image files on high confidence.

### What it does
For each image that has a Tier 2 description:
1. Sends the image to Google Vision API for artwork identification
2. Writes a `.nfo` sidecar file with identification results
3. On high confidence: performs a transactional rename of the image file
4. Updates the DB path record via existing moved-file detection

### Settings
```
google:vision_api_key        — API key (required)
art:enrichment_requests_per_minute  — default: 5
art:enrichment_confidence_threshold — default: 0.85
art:enrichment_retry_hours          — hours before retrying a failed lookup, default: 24
```

---

## Sidecar Format — `.nfo` files

Every processed image gets a sidecar named `<original_filename>.nfo`.
Format is INI-style — human-readable and directly editable by the user.

```ini
[artwork]
artist        = Claude Monet
title         = Water Lilies
year          = 1906
medium        = Oil on canvas
collection    = Impressionism Movement of the World - 1860-1920
confidence    = 0.94
source_url    = https://...
identified_at = 2026-03-07
original_name = 0045.jpg
renamed_to    = Claude Monet - Water Lilies.jpg
```

### States

| confidence field | renamed_to field | Meaning |
|---|---|---|
| >= 0.85 | `<Artist> - <Title>.jpg` | Identified and renamed |
| < 0.85 | `(low confidence — manual review)` | Found something, not confident enough to rename |
| — | `(failed)` | Lookup failed or errored — will retry after `art:enrichment_retry_hours` |
| — | `(pending)` | Queued, not yet processed |

The `.nfo` file IS the state record. Its presence means the file has been attempted.
Its absence means the file is unprocessed and eligible for queuing.

### Vault scanner
`.nfo` extension is added to the vault scanner's ignore list so these sidecars are
never ingested as documents themselves.

---

## Rename — Transactional Protocol

Only executes when `confidence >= art:enrichment_confidence_threshold`.

```
BEGIN
  1. Compute target name
     - Pattern: <Artist Name> - <Title>.<ext>
     - If no clear title: <Artist Name> - Unnamed.<ext>
     - Sanitise: strip illegal filesystem chars, truncate to 200 chars
     - Collision: if target exists, append _(2), _(3)...
  2. Update .nfo: set renamed_to = <target name>
  3. os.rename(original_path, target_path)
  4. VERIFY: os.path.exists(target) AND NOT os.path.exists(original)

COMMIT
  - Update DB tasks row: file_path = new path (file_hash unchanged)
  - Log success

ROLLBACK (any step fails)
  - If file was renamed: os.rename(target, original)  [restore]
  - Update .nfo: renamed_to = (failed)
  - Log failure with reason
  - Do not automatically retry — wait art:enrichment_retry_hours
```

The DB update uses the existing moved-file detection path (same SHA-256 hash, new path).
No new DB machinery required.

---

## Throttling & Rate Limiting

- Tier 3 only runs when `ThrottleStateMachine` is in IDLE state
- Hard cap: `art:enrichment_requests_per_minute` (default 5) — enforced with a token bucket
- On HTTP 429 from Google: exponential backoff starting at 60s, max 1 hour
- On quota exhaustion: pause for remainder of day, resume next UTC midnight
- Per-image cooldown: `art:enrichment_retry_hours` before re-attempting a failed lookup
- Log every API call to `logs.db` `worker_log` table for audit

---

## State Tracking

**Option chosen: `.nfo` presence as state** (simplest, no schema change)

| State | How detected |
|---|---|
| Unprocessed | No `.nfo` file alongside the image |
| Attempted (any result) | `.nfo` file exists |
| Ready to rename | `.nfo` exists, `confidence >= threshold`, `renamed_to` not yet applied |
| Complete | `.nfo` exists, `renamed_to` matches current filename |
| Failed / retry pending | `.nfo` exists, `renamed_to = (failed)` |

The enrichment worker scans for image files without a corresponding `.nfo` to build its queue.

---

## Implementation Order

1. **Tier 1** — `art_collection_extractor.py` (folder kernel, fast, no AI)
2. **Settings** — add `google:vision_api_key` and `art:*` settings to schema
3. **Vault scanner** — add `.nfo` to ignore list
4. **Tier 2** — wire image child tasks into idle worker (may reuse existing infrastructure)
5. **Tier 3** — `art_enrichment_worker.py` (new idle daemon) + Google Vision API client
6. **UI** — enrichment progress visible somewhere (Utilities page or new Art tab)

---

## Open Questions (deferred)

- Should the Google Vision API key be shared with other future Google integrations, or art-specific?
- When the user manually edits a `.nfo` file and corrects artist/title, should the next enrichment pass honour that and trigger a rename? (Likely yes — detect `renamed_to = (manual)` as a signal.)
- Tier 2 full scan produces a lot of Ollama calls for large collections. Should there be a per-collection daily cap?
