# Deleted-File Detection — Design Spec
**Date:** 2026-07-19
**Status:** Approved

---

## 1. Problem

`core/ingestor.py::ingest()` walks each active vault's `scan_directory` on a recurring cycle (`run.py::ingestion_worker_run`, default every 60s) and detects new and moved files via the `file_vault(file_hash, vault_id, file_path)` table. It never detects the opposite case: a file that used to exist and no longer does. When a source file is deleted, its `tasks` row, `extracted_texts` row, FTS chunks, extracted images, and vector embedding all remain forever — the file keeps appearing in search results and RAG answers pointing at a path that no longer resolves to anything.

## 2. Goals

- Detect, per vault, when a previously-known file is no longer found during a scan.
- Do not destroy any extracted work (text, images, embeddings) on first detection — that work may be expensive to regenerate and the absence may be transient (network drive hiccup, drive-letter change, temporarily unmounted removable media).
- Correctly handle content registered in more than one vault (`file_vault` supports one row per `file_hash` × `vault_id`): losing the file in one vault's directory must not hide it if another vault still has a working copy.
- Exclude confirmed-missing files from search and RAG results without touching the LanceDB query-construction path that was already the source of a real 30s+ performance regression (`common-bugs.md`, the giant-`IN`-clause fix).
- Give the user a way to see what's flagged and permanently purge it once they've confirmed the deletion was real.
- Automatically un-flag a file if it reappears, restoring it to a sane status rather than a stale in-flight one.

## 3. Non-Goals

- Automatic permanent deletion. Nothing in this feature deletes a task's data without an explicit user action on the new Missing Files panel.
- Detecting deletion of the folder-level "Directory Unit" tasks (`DIR_...` hashes) created by `ingest()`'s folder-intelligence pass. Out of scope — those are a separate task kind with different lifecycle semantics.
- Any change to `VaultManager._gut_vault`'s existing behavior or call sites, beyond factoring its batch-delete logic into a function this feature also calls (see §7).

## 4. Schema Changes (`core/manager.py::init_db`)

```sql
ALTER TABLE file_vault ADD COLUMN miss_count INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN pre_missing_status TEXT;
```

Both added via the existing `_migrate_schema()`-style idempotent `ALTER TABLE ... ADD COLUMN` pattern already used elsewhere in `init_db()` (checked against `PRAGMA table_info` before adding, matching how `vault_id`/`parent_hash` were added to `tasks` historically per `infrastructure-migration.md`).

`tasks.status` gains one new valid value: `'MISSING'`, alongside the existing `PENDING | PROCESSING | EXTRACTED | EMBEDDING | COMPLETED | ERROR`.

New setting in `core/settings.py`, group `ingestion`:

```python
'ingestion:missing_after_scans': {
    'type': 'int', 'default': 3, 'label': 'Missing after N scans', 'group': 'ingestion',
    'description': 'A file must be absent for this many consecutive vault scans before it is '
                   'flagged MISSING. Protects against transient issues (network drive hiccups, '
                   'drive-letter changes) causing a false flag. Default: 3 (~3 minutes at the '
                   'default 60s scan interval).',
},
```

## 5. Detection (`core/ingestor.py::ingest`)

After the existing per-file walk loop completes for a vault, add a second pass:

1. Read all `file_vault` rows for this `vault_id` (`file_hash`, `file_path`, `miss_count`).
2. For every row whose `file_path` was confirmed present during this walk (tracked via a `seen_paths: set[str]` populated alongside the existing per-file loop): reset `miss_count = 0` if it was nonzero.
3. For every row *not* seen this walk: `miss_count += 1`.
4. For any row whose `miss_count` just crossed `ingestion:missing_after_scans`: check whether *every* `file_vault` row for that `file_hash` (across all vaults) now has `miss_count >= threshold`. If so, and `tasks.status != 'MISSING'` already: set `pre_missing_status = status`, `status = 'MISSING'`.
5. For any row whose `miss_count` just reset to 0 (file reappeared) where `tasks.status == 'MISSING'`: restore status per the mapping in §6.

Steps 2-5 run as a single batch per vault scan (one read of that vault's `file_vault` rows, one set of updates), not per-file-during-the-walk, since step 4's "every vault" check needs the full picture after the walk, not a partial one.

## 6. Restore Logic

When a `file_vault` row's `miss_count` resets to 0 and the task is currently `MISSING`:

| `pre_missing_status` was | Restore to | Why |
|---|---|---|
| `PENDING`, `EXTRACTED`, `COMPLETED`, `ERROR` | itself, verbatim | Safe — no in-flight work to resume incorrectly. |
| `PROCESSING` | `PENDING` | An extraction worker may have been mid-run when the file vanished; safest to re-run extraction from scratch rather than assume partial progress. |
| `EMBEDDING` | `EXTRACTED` | Extraction had already completed and is stored in `extracted_texts`; re-queue for embedding only. |

`pre_missing_status` is cleared to `NULL` after restoring.

## 7. Purging (hard delete)

`VaultManager._gut_vault` already implements a batched hard-delete (fts_index → extracted_images → tasks + file_vault → vectors) for an entire vault's hashes. This feature needs the same delete shape for an arbitrary, caller-supplied list of hashes (the ones a user selects on the Missing Files panel), not "everything in vault X." Rather than duplicating that batch-delete logic, extract it:

```python
# core/manager.py
def purge_file_hashes(db_path: str, file_hashes: list[str]) -> None:
    """Batched hard-delete of tasks + fts_index + extracted_images + file_vault
    rows for the given hashes. extracted_texts cascades via its existing FK.
    Does not touch the vector store — callers do that themselves (best-effort,
    should never block the SQL delete)."""
```

`VaultManager._gut_vault` is refactored to call `manager.purge_file_hashes(self.db_path, hashes)` for its steps 2-3, then continues to do its own vector cleanup exactly as it does today — no behavior change to `_gut_vault`, just a shared implementation.

The new missing-files purge endpoint (§8) calls `purge_file_hashes`, but only ever passes hashes it has independently verified have `status = 'MISSING'` — never a caller-supplied hash list applied blindly.

## 8. API (`api/routes/missing_files.py`, new file)

Mirrors the existing `api/routes/art.py` shape (list/purge over a live worklist):

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/missing_files` | GET | List `MISSING` tasks. Query params: `vault_id`, `q` (filename substring), `limit`, `offset`. |
| `/api/missing_files/purge` | POST | Body `{hashes: [...]}` or `{all: true, vault_id?, q?}` — `"all"` means every row matching the panel's current filters, not the whole table. Verifies each hash's status is still `MISSING` before calling `purge_file_hashes`, then best-effort removes vectors via `VectorStore().delete_by_hashes(...)`. |

Registered in `api/main.py` alongside the other routers.

## 9. Excluding MISSING from Search

- **FTS / regex search** (`core/manager.py::fts_search`, `search/fts.py::_regex_search` — both have a vault-filtered and non-vault-filtered branch, 4 call sites total): add `AND t.status != 'MISSING'` (or `tasks.status != 'MISSING'` per the existing alias in that branch) to each WHERE-clause list. Plain SQLite, no perf concern.
- **Semantic / hybrid search:** deliberately *not* via a LanceDB hash filter — that is the exact shape of the bug fixed in `9522b5e` (giant `IN (...)` clauses stalling LanceDB's query planner for 30s+ on large vaults). Instead: fetch the current `MISSING` hash set once per query (`SELECT file_hash FROM tasks WHERE status = 'MISSING'` — expected to be a small set relative to total corpus size) and post-filter LanceDB's already-returned ANN candidates in Python, in `search/semantic.py` (or wherever `hybrid.py` merges results), before scoring/merging.
- **Vault Log / catalog browsing** (`list_tasks`): left unchanged. `MISSING` becomes an ordinary visible status there, same as `ERROR` or `COMPLETED` — that view is diagnostic, not a "clean" content browser, so seeing what's flagged there is useful, not noise. The dedicated Missing Files panel (§10) is the actionable view.
- **Worker claim queries** (`claim_pending_task`, `claim_extracted_task`, etc.): no change needed. They already filter on specific statuses (`WHERE status = 'PENDING'` etc.), so a `MISSING` task is automatically never claimed.

## 10. UI (`frontend/vault.html`)

New accordion panel, same `lc-bar`/`lc-bar-expand` structure as the Vector Store / Diagnostics panels already on this page (and the same shape as the recent Art Enrichment Issues panel):

- Filter row: vault dropdown, text search (filename).
- Table: checkbox column + select-all, file path, vault(s) it was registered in, last-seen timestamp (derived from `last_update`), `pre_missing_status` (so the user knows what state it'll restore to, or would have purged from).
- Action bar: **Purge selected**, **Purge all** (confirm-gated two-step modal, matching `settings.html`'s Rebuild Index pattern).
- No "reprocess" action (unlike Art Enrichment Issues) — there is nothing to reprocess; the only actions are wait-for-restore (automatic) or purge (manual).

i18n strings added under a new `vault.missing.*` key namespace in `en.json`/`es.json`/`fr.json`.

## 11. Rollout

No data migration beyond the two `ALTER TABLE ADD COLUMN`s (both default to falsy/NULL, so existing rows are unaffected until the next scan cycle touches them). No existing behavior changes for files that are actually still present — `miss_count` only ever increments for rows genuinely absent from a walk. The feature is entirely additive until a real deletion occurs.
