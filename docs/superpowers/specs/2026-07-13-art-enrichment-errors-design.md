# Art Enrichment Errors Tracking & UI — Design Spec
**Date:** 2026-07-13
**Status:** Approved

---

## 1. Problem

`workers/art_enrichment_worker.py` records the outcome of every art-identification attempt (success, low-confidence, or hard failure) only as a per-image `.nfo` sidecar file on disk — there is no database record of what succeeded or failed. When a systemic failure occurs (e.g. the 2026-07-13 Google Cloud billing-disabled incident), the only way to find affected files is to walk the entire vault filesystem reading every `.nfo`. Over a ~187K-file vault on a network mount, that scan takes many minutes and cannot be filtered, paginated, or acted on from the UI.

---

## 2. Goals

- A fast, filterable, paginated view of every outstanding art-enrichment issue (hard failures and low-confidence-needs-review results).
- Select one or more issues and either **reprocess** them (force the worker to re-attempt from scratch) or **dismiss** them (clear from the list without touching the file).
- A **dismiss all** action to control table bloat over time.
- A one-time (and re-runnable) backfill that populates the table from the vault's existing `.nfo` files.
- The worker's existing automatic retry (`art:enrichment_retry_hours`, default 24h) is untouched — this UI is a manual-override/visibility layer on top of it, not a replacement.

## 3. Non-Goals

- Tracking successful identifications in the new table. Successes are already visible via the renamed file itself; duplicating that in a DB row adds bloat with no benefit for this feature.
- Changing how `.nfo` files are structured or how the worker decides retry eligibility today.
- A general-purpose "all system errors" page — this is specific to art enrichment.

---

## 4. Where the table lives: `docvault.db`

Recommended over `logs.db` or a new database file. `logs.db`'s tables (`worker_errors`, `worker_log`, `task_timings`) are append-only audit trails of history. This table is a **live worklist** — rows are added and removed as issues get reprocessed or dismissed, representing current state, not history. That shape matches `docvault.db`'s existing per-file worker-output tables (`face_detections`, `extracted_images`), not `logs.db`'s history tables. Consequence: `reset.ps1` wipes this table along with the rest of `docvault.db` — acceptable, since it's fully rebuildable from `.nfo` files via the rescan endpoint.

---

## 5. Schema

```sql
CREATE TABLE IF NOT EXISTS art_enrichment_issues (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash    TEXT,              -- looked up from tasks by file_path; NULL if no match found
    file_path    TEXT NOT NULL UNIQUE,
    vault_id     TEXT,
    status       TEXT NOT NULL,     -- 'failed' | 'review'
    tier         TEXT,              -- 'clip' | 'google' | 'bing' | ''
    artist       TEXT,
    title        TEXT,
    confidence   REAL,
    error        TEXT,              -- populated for status='failed'; includes HTTP response body
    occurred_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_art_issues_status   ON art_enrichment_issues(status);
CREATE INDEX IF NOT EXISTS idx_art_issues_vault_id ON art_enrichment_issues(vault_id);
```

`file_path` (not `file_hash`) is the natural unique key: the worker operates path-first, and a failing image shouldn't require an extra hash lookup on every write just to have a primary key. Writes are `INSERT ... ON CONFLICT(file_path) DO UPDATE` — re-failing the same file refreshes its row instead of duplicating it. `file_hash` is looked up from `tasks` by `file_path` (already indexed via `idx_tasks_file_path`) for convenience linking to the rest of the app; `NULL` if no match is found (e.g. file not yet ingested as a task).

Added to `core/manager.py`'s `init_db()`, following the existing `CREATE TABLE IF NOT EXISTS` + index pattern.

---

## 6. Worker integration (`workers/art_enrichment_worker.py`)

New helper:

```python
def _track_issue(image_path: str, vault_id: str, status: str, tier: str,
                  artist: str, title: str, confidence: float, error: str, db_path: str):
    """Upsert an outstanding issue row, or clear it on success."""
```

Called at the existing `_write_nfo(...)` call sites for the `(failed)` and `(low confidence — manual review)` outcomes — never on the successful-rename path. On a successful rename, if a row for that `file_path` already exists (a previously-tracked file that just got fixed), it is deleted — the table only ever reflects currently outstanding issues, never resolved ones.

`vault_id` is resolved the same way the worker already resolves `vault_dirs` (via `VaultManager`) — the vault whose `scan_directory` contains `image_path`.

---

## 7. API endpoints (new `api/routes/art.py`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/art/issues` | GET | List issues. Query params: `status`, `vault_id`, `q` (text search on artist/title/file_path), `limit`, `offset`. Mirrors `catalog.py`'s list pattern. |
| `/api/art/issues/reprocess` | POST | Body `{ids: [...]}`. Deletes the `.nfo` file and the DB row for each — the worker's normal `_find_unprocessed` scan picks them up fresh next cycle. |
| `/api/art/issues/dismiss` | POST | Body `{ids: [...]}` or `{all: true, status?, vault_id?, q?}`. "Dismiss all" always means every row matching the UI's *current* filter selection, not the entire table — the frontend passes its active filters through unchanged, so what you see is what gets cleared. Deletes DB rows only — `.nfo` and file untouched. |
| `/api/art/issues/rescan` | POST | Triggers a background-thread vault walk that backfills the table from existing `.nfo` files (same fire-and-forget pattern as `/utils/build_vector_index`). Also the mechanism used for the one-time backfill right after this ships. |

Registered in `api/main.py` alongside the other routers.

---

## 8. Frontend (`frontend/utils.html`)

New accordion panel following the existing `lc-bar`/`lc-bar-expand` pattern (same one used for the Vector Store and Diagnostics panels):

- Filter row: status dropdown (All / Failed / Review), vault dropdown, text search.
- Table: checkbox column (+ select-all), file path, status badge, artist/title (if any), error (truncated, for failed rows), occurred_at.
- Action bar: **Reprocess selected**, **Dismiss selected**, **Dismiss all** (confirm-gated, two-step modal matching `settings.html`'s Rebuild Index confirm pattern), **Rescan vault** (triggers backfill, shows a "running" state — this can take minutes on a large vault).
- Pagination (reuse whatever pattern `vault.html`'s Vault Log table already uses, since this is structurally the same kind of view).

i18n strings added to `en.json`/`es.json`/`fr.json` following the existing `utils.*` key namespace.

---

## 9. Backfill

The rescan walks each active/archived vault's `scan_directory`, reads every `.nfo` whose `renamed_to` is `(failed)` or `(low confidence — manual review)`, and upserts a row per the schema above (status/tier/artist/title/confidence/error parsed from the `.nfo`'s `[artwork]` section). Runs in a background thread; the endpoint returns immediately. This same endpoint is what gets triggered once, manually, to seed the table from the vault's current state after this ships.
