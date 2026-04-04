# Multi-Vault File Membership Design

## Goal

Allow a single file (identified by SHA-256 hash) to belong to multiple vaults simultaneously, so that files accessible via different paths (e.g., a local mount and a NAS UNC path) appear in searches scoped to any vault that contains them.

## Background

DocVault uses `file_hash` as the primary key for the `tasks` table. The current `INSERT OR IGNORE` ingestor logic means the second vault to encounter a file silently loses — the task row stays under the origin vault, and the second vault's scan directory registers zero tasks for those files. This became visible when PhotoTest (NAS UNC path) found the same photos already indexed under My Docs (Z: mount).

## Architecture

A new junction table `file_vault` records every (file, vault) pair, storing the path as seen from that vault. All vault-scoped filtering queries join through this table instead of `tasks.vault_id`. The `tasks.vault_id` column is preserved as the origin vault for workers, logs, and timing records — no change to extraction or embedding logic.

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

**Migration:** After table creation, backfill from existing tasks:
```sql
INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id IS NOT NULL;
```

## Components

### `core/manager.py`

**New function `upsert_file_vault(db_path, file_hash, vault_id, file_path)`:**
- `INSERT OR REPLACE` into `file_vault`
- No-op if `vault_id` is None
- Called by ingestor on every file encounter (new and already-known)

**New function `get_vault_paths(db_path, file_hashes, vault_id)`:**
- Returns `{file_hash: file_path}` from `file_vault` for the given vault and set of hashes
- Used to substitute canonical paths with vault-specific paths in search results

**`get_filtered_hashes()` change:**
- When `vault_ids` is set, query `file_vault` instead of `tasks`:
  ```sql
  SELECT DISTINCT file_hash FROM file_vault WHERE vault_id IN (...)
  ```

**`fts_search()` change:**
- When `vault_ids` is set, join `file_vault`:
  ```sql
  JOIN file_vault fv ON t.file_hash = fv.file_hash AND fv.vault_id IN (?, ?)
  ```
- SELECT uses `fv.file_path AS file_path` instead of `t.file_path`

**`_filename_filter_clauses()` change:**
- When `vault_ids` is set, replace `vault_id IN (...)` with:
  ```sql
  file_hash IN (SELECT file_hash FROM file_vault WHERE vault_id IN (...))
  ```

### `core/ingestor.py`

**New file** (`existing is None`): after `manager.insert_task(...)`, call `manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)`.

**Already-known file, same path** (currently a no-op): call `manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)`. This is the fix — registering the second vault's path even when the task row already exists.

**Already-known file, moved** (path mismatch within same vault): unchanged — a move is not a new vault membership.

**Directory tasks** (folder intelligence): also call `upsert_file_vault` after `insert_task` for directory units.

### `search/hybrid.py` and `search/semantic.py`

When a vault filter is active (single vault_id), after results are returned, call `manager.get_vault_paths()` and replace `file_path` in each result with the vault-specific path. This ensures results shown to the user reflect the path accessible within the filtered vault.

Only applied when exactly one vault is selected (multi-vault filter shows the canonical path).

## Data Flow

```
Ingestor scans PhotoTest NAS path
  → file_hash already in tasks (origin: My Docs, path: Z:\...)
  → upsert_file_vault(hash, PhotoTest vault_id, //192.168.1.11/...path)

User searches, filters to PhotoTest
  → get_filtered_hashes joins file_vault → returns hash (PhotoTest membership found)
  → fts_search joins file_vault → result has file_path = //192.168.1.11/...
  → semantic results: get_vault_paths substitutes file_path for PhotoTest
```

## Error Handling

- `upsert_file_vault` is a best-effort write; ingestor wraps it in the existing `except Exception` block — a failure logs a warning but does not abort the scan
- `get_vault_paths` returns an empty dict on DB error; caller keeps canonical path

## Testing

`tests/test_multi_vault_membership.py` — 10 tests:

1. `test_file_vault_table_created` — `init_db()` creates `file_vault`
2. `test_backfill_on_migration` — existing tasks rows backfilled into `file_vault`
3. `test_upsert_file_vault_new` — new file: both `tasks` and `file_vault` rows written
4. `test_upsert_file_vault_existing_task` — already-indexed file gets second `file_vault` row without touching task row
5. `test_upsert_file_vault_path_update` — re-scan updates `file_vault.file_path` via `INSERT OR REPLACE`
6. `test_get_filtered_hashes_multi_vault` — hash appears under both vault A and vault B
7. `test_fts_search_vault_filter_returns_vault_path` — FTS result shows NAS path when filtering to PhotoTest vault
8. `test_filename_search_vault_filter` — filename search scoped to correct vault
9. `test_get_vault_paths` — helper returns correct per-vault paths for a set of hashes
10. `test_ingestor_registers_second_vault` — integration: same file, two vaults, both registered in `file_vault`
