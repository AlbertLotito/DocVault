# Multi-Vault File Membership Design

## Goal

Allow a single file (identified by SHA-256 hash) to belong to multiple vaults simultaneously, so that files accessible via different paths (e.g., a local mount and a NAS UNC path) appear in searches scoped to any vault that contains them, with results showing the path within the filtered vault.

## Background

DocVault uses `file_hash` as the primary key for the `tasks` table. The current `INSERT OR IGNORE` ingestor logic means the second vault to encounter a file silently loses — the task row stays under the origin vault, and the second vault's scan directory registers zero tasks for those files. This became visible when PhotoTest (NAS UNC path `//192.168.1.11/home/Photos/...`) found the same photos already indexed under My Docs (Z: mount).

## Architecture

A new junction table `file_vault` records every (file, vault) pair, storing the path as seen from that vault. All vault-scoped filtering queries join through this table. The `tasks.vault_id` column is preserved as the origin vault for workers, logs, and timing records — extraction and embedding logic is unchanged.

## Schema

New table in `docvault.db`, created in `init_db()`:

```sql
CREATE TABLE IF NOT EXISTS file_vault (
    file_hash TEXT NOT NULL REFERENCES tasks(file_hash),
    vault_id  TEXT NOT NULL REFERENCES vaults(vault_id),
    file_path TEXT NOT NULL,
    added_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (file_hash, vault_id)
);
```

`file_path` is the path as seen within this vault (e.g. `//192.168.1.11/home/Photos/photo.jpg` for the NAS vault, `Z:\Documents\Photos\photo.jpg` for the local vault).

**Migration — backfill in `init_db()`:** After table creation, backfill from existing tasks:
```sql
INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id IS NOT NULL;
```

**Migration — backfill in `bootstrap_default_vault()`:** After the `UPDATE tasks SET vault_id = ? WHERE vault_id IS NULL` step, also run:
```sql
INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id = ?;
```
This covers the pre-vault-feature upgrade path where `init_db()` runs before `bootstrap_default_vault()` assigns vault_ids to legacy tasks.

## Components

### `core/manager.py`

**New function `upsert_file_vault(db_path, file_hash, vault_id, file_path)`:**
```python
def upsert_file_vault(db_path, file_hash, vault_id, file_path):
    if not vault_id:
        return
    norm = os.path.normpath(file_path)   # normalise before storage
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO file_vault (file_hash, vault_id, file_path)
               VALUES (?, ?, ?)
               ON CONFLICT(file_hash, vault_id) DO UPDATE SET file_path = excluded.file_path""",
            (file_hash, vault_id, norm)
        )
        conn.commit()
```
Normalises `file_path` with `os.path.normpath` before storing, so the comparison `os.path.normpath(vault_path) != file_path` in the ingestor is always between two normalised paths. Uses `ON CONFLICT DO UPDATE` (SQLite 3.24+) — `added_at` is not in the SET clause so it is naturally preserved on re-scan.

**New function `get_vault_paths(db_path, file_hashes, vault_id)`:**
```python
def get_vault_paths(db_path, file_hashes, vault_id):
    """Returns {file_hash: file_path} from file_vault for the given vault."""
    if not file_hashes:
        return {}
    try:
        placeholders = ','.join(['?'] * len(file_hashes))
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT file_hash, file_path FROM file_vault WHERE vault_id = ? AND file_hash IN ({placeholders})",
                [vault_id, *file_hashes],
            ).fetchall()
        return {r['file_hash']: r['file_path'] for r in rows}
    except Exception:
        return {}
```
Returns `{}` on any DB error — callers keep canonical path. Uses parameterised `IN` clause (not string interpolation) to avoid SQL injection.

**New function `get_file_vault_path(db_path, file_hash, vault_id)`:**
```python
def get_file_vault_path(db_path, file_hash, vault_id):
    """Returns the stored file_path for (file_hash, vault_id) in file_vault, or None."""
    if not vault_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path FROM file_vault WHERE file_hash = ? AND vault_id = ?",
            (file_hash, vault_id),
        ).fetchone()
    return row['file_path'] if row else None
```
Used by the ingestor to check whether a file is already registered for a given vault before deciding if a move has occurred.

**`get_filtered_hashes()` change:**
- When `vault_ids` is set, query `file_vault` instead of `tasks`:
  ```sql
  SELECT DISTINCT file_hash FROM file_vault WHERE vault_id IN (...)
  ```

**`fts_search()` change:**
The existing `tasks.vault_id IN (...)` WHERE clause (currently added when `vault_ids` is set) is **removed entirely** and replaced with an INNER JOIN on `file_vault`. Do not keep both — they would conflict.

When `vault_ids` is set:
- Remove: `where.append(f"tasks.vault_id IN (...)")` (existing line ~730)
- Add to FROM clause: `JOIN file_vault fv ON t.file_hash = fv.file_hash AND fv.vault_id IN (?, ?)`
- In SELECT: replace `fts_index.file_path` with `fv.file_path AS file_path`

When `vault_ids` is None (no filter): keep the existing SELECT with `fts_index.file_path` unchanged — no JOIN added.

INNER JOIN is correct for the vault-filtered case: every FTS result must have a `file_vault` entry for the requested vault (that is how it was included in the result set).

**`_regex_search()` change (in `search/fts.py`):**
Same treatment as `fts_search()`: the existing `tasks.vault_id IN (...)` WHERE clause is **removed** and replaced with an INNER JOIN on `file_vault`, `fv.file_path` in SELECT, only when `vault_ids` is set.

**`_filename_filter_clauses()` change:**
- When `vault_ids` is set, replace `vault_id IN (...)` with:
  ```sql
  file_hash IN (SELECT file_hash FROM file_vault WHERE vault_id IN (...))
  ```
- **Also fix the call site in `filename_search()` plain-text branch** (currently passes only 3 args to `_filename_filter_clauses`, omitting `vault_ids`): pass `vault_ids=vault_ids` as the fourth argument. This is a pre-existing omission that becomes a correctness bug once the helper is updated.

### `core/ingestor.py`

The ingestor's move-detection logic currently compares `existing['file_path']` (origin vault path in `tasks`) against the scan path. In a multi-vault setup, a second vault scanning the same file will always trigger this condition (NAS path ≠ Z: path). To avoid corrupting `tasks.file_path` with a non-origin-vault path, the logic must be vault-aware.

**Revised ingestor branching logic** (replaces the current `if/elif` on `existing`):

```python
existing = manager.get_task(db_path, file_hash)

if existing is None:
    # Brand-new file — create task and register vault membership
    manager.insert_task(db_path, file_hash, file_path, ...)
    manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)

else:
    vault_path = manager.get_file_vault_path(db_path, file_hash, vault_id)

    if vault_path is None:
        # File known globally but not yet registered in this vault
        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)

    elif os.path.normpath(vault_path) != file_path:
        # File has moved within this vault — update vault-specific path
        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
        # Only update tasks.file_path (canonical) if this is the origin vault
        if existing['vault_id'] == vault_id:
            manager.update_task_path(db_path, file_hash, file_path)
            _update_qdrant_path(file_hash, file_path)

    # else: known file at known vault path — nothing to do
```

This ensures `tasks.file_path` (the canonical origin path) is only modified when the origin vault detects a move. Non-origin vaults update only their `file_vault` row.

**Directory tasks** (folder intelligence): also call `upsert_file_vault(db_path, folder_hash, vault_id, root_norm)` after `insert_task` for directory units. `root_norm` is the normalised folder path already computed at that point in the ingestor loop. `folder_hash` is a `DIR_<md5>` string — not a real SHA-256 but treated consistently as a task identifier.

**Child tasks (`insert_child_tasks()`):** Excluded from this design. Child tasks are created by extractors (not the ingestor walk), are internal processing units, and are not user-searchable as standalone files. Their `vault_id` is set at insertion and they will be covered by the `init_db()` backfill migration on upgrade.

### Path substitution — where it happens

When a vault filter is active (exactly one vault_id selected), search results must show the vault-specific path from `file_vault.file_path` rather than the canonical origin path.

**FTS results:** Already handled by the `fv.file_path` join change in `fts_search()` and `_regex_search()`.

**Semantic and hybrid results:** Path substitution happens in the **route handler** (`api/routes/search.py` for semantic/hybrid modes, `api/routes/query.py` for RAG). After results are returned, the handler calls `manager.get_vault_paths(db_path, {r['file_hash'] for r in results}, vault_id_list[0])` and replaces `file_path` in each result. This keeps `semantic.py` and `hybrid.py` signatures unchanged.

Applied only when `len(vault_id_list) == 1`. Multi-vault filter shows the canonical path (no substitution).

**Hybrid merging note:** In `hybrid.async_search()`, the RRF merge prefers the semantic result payload (`sem_data.get(key) or fts_data[key]`). After the merge, the final result list may carry Qdrant-payload paths. Path substitution in the route handler runs on the final merged list, so it correctly patches all results regardless of which source won the merge.

## Data Flow

```
Ingestor scans PhotoTest NAS path
  → file_hash already in tasks (origin: My Docs, path: Z:\...)
  → upsert_file_vault(hash, PhotoTest vault_id, //192.168.1.11/...path)
  → file_vault now has two rows for this hash (My Docs + PhotoTest)

User searches, filters to PhotoTest
  → get_filtered_hashes → SELECT DISTINCT file_hash FROM file_vault WHERE vault_id = PhotoTest
  → fts_search → INNER JOIN file_vault → result.file_path = //192.168.1.11/...
  → semantic results → route handler calls get_vault_paths → substitutes file_path
  → user sees NAS path in results
```

## Error Handling

- `upsert_file_vault` is best-effort; ingestor wraps it in the existing `except Exception` block — failure logs a warning but does not abort the scan
- `get_vault_paths` returns `{}` on DB error; caller keeps canonical path
- INNER JOIN in `fts_search` with `vault_ids` is safe because `get_filtered_hashes` already ensures only hashes with `file_vault` entries pass through

## Testing

`tests/test_multi_vault_membership.py` — 12 tests:

1. `test_file_vault_table_created` — `init_db()` creates `file_vault`
2. `test_backfill_on_migration` — existing tasks rows backfilled into `file_vault`
3. `test_upsert_file_vault_new` — new file: both `tasks` and `file_vault` rows written
4. `test_upsert_file_vault_existing_task` — already-indexed file gets second `file_vault` row without touching task row
5. `test_upsert_file_vault_path_update` — re-scan updates `file_vault.file_path` but preserves `added_at`
6. `test_get_filtered_hashes_multi_vault` — hash returned for both vault A and vault B filters
7. `test_fts_search_vault_filter_returns_vault_path` — FTS result shows NAS path when filtering to PhotoTest vault
8. `test_filename_search_vault_filter` — filename search scoped to correct vault
9. `test_get_vault_paths` — helper returns correct per-vault paths for a set of hashes
10. `test_ingestor_registers_second_vault` — integration: same file, two vaults, both registered in `file_vault`
11. `test_multi_vault_filter_no_path_substitution` — when two vault IDs are selected, canonical path is returned (no substitution)
12. `test_ingestor_move_non_origin_vault_does_not_corrupt_tasks_path` — when a non-origin vault detects a path change, `tasks.file_path` is NOT updated; only `file_vault.file_path` changes
