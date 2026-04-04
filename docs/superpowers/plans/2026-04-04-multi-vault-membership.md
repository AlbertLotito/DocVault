# Multi-Vault File Membership Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a single file (by SHA-256 hash) to belong to multiple vaults simultaneously, so files accessible via different paths (e.g. local Z: mount and NAS UNC path) appear in searches scoped to any vault that contains them, showing the vault-specific path.

**Architecture:** A new junction table `file_vault(file_hash, vault_id, file_path)` records every (file, vault) pair. All vault-scoped filtering queries join through this table instead of `tasks.vault_id`. The ingestor is made vault-aware so it registers a file in `file_vault` on every encounter, even when the `tasks` row already exists under a different vault.

**Tech Stack:** Python 3.11, SQLite 3.24+ (ON CONFLICT DO UPDATE), FastAPI, pytest

---

## Chunk 1: Schema, Manager Helpers, and Filtering

### Task 1: Schema, migrations, and manager helpers

**Files:**
- Modify: `core/manager.py` (init_db lines 192–285, bootstrap_default_vault lines 315–319, add new functions after insert_child_tasks ~line 360)
- Create: `tests/test_multi_vault_membership.py`

#### Key context for implementer

`init_db()` uses `conn.executescript()` for DDL (lines 192–256), then `conn.execute()` for ALTER TABLE migrations (258–285), then `conn.commit()`. `bootstrap_default_vault()` runs after `init_db()` in `run.py`. The `_connect()` context manager is at line 178.

- [ ] **Step 1: Write failing tests 1–5 and 9**

Create `tests/test_multi_vault_membership.py`:

```python
import pytest
import os
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def vault_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'Vault A', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-b', 'Vault B', '//nas/photos', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


# ── Test 1 ─────────────────────────────────────────────────────────────────────

def test_file_vault_table_created(db):
    with _connect(db) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
    assert 'file_vault' in tables


# ── Test 2 ─────────────────────────────────────────────────────────────────────

def test_backfill_on_migration(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('abc', '/docs/a.pdf', 'pdf', 'v1')")
        conn.commit()
    manager.init_db(db_path)  # re-run triggers backfill
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM file_vault WHERE file_hash='abc'").fetchone()
    assert row is not None
    assert row['vault_id'] == 'v1'
    assert row['file_path'] == '/docs/a.pdf'


# ── Test 3 ─────────────────────────────────────────────────────────────────────

def test_upsert_file_vault_new(vault_db):
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.commit()
    manager.upsert_file_vault(vault_db, 'h1', 'vault-a', '/docs/a.txt')
    with _connect(vault_db) as conn:
        row = conn.execute("SELECT * FROM file_vault WHERE file_hash='h1' AND vault_id='vault-a'").fetchone()
    assert row is not None
    assert row['file_path'] == os.path.normpath('/docs/a.txt')


# ── Test 4 ─────────────────────────────────────────────────────────────────────

def test_upsert_file_vault_existing_task(vault_db):
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', '/docs/a.txt')")
        conn.commit()
    manager.upsert_file_vault(vault_db, 'h1', 'vault-b', '//nas/a.txt')
    with _connect(vault_db) as conn:
        rows = conn.execute("SELECT * FROM file_vault WHERE file_hash='h1'").fetchall()
        task = conn.execute("SELECT vault_id, file_path FROM tasks WHERE file_hash='h1'").fetchone()
    assert len(rows) == 2
    assert task['vault_id'] == 'vault-a'   # tasks row unchanged
    assert task['file_path'] == '/docs/a.txt'


# ── Test 5 ─────────────────────────────────────────────────────────────────────

def test_upsert_file_vault_path_update_preserves_added_at(vault_db):
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path, added_at) VALUES ('h1', 'vault-a', '/docs/a.txt', '2024-01-01T00:00:00')")
        conn.commit()
    manager.upsert_file_vault(vault_db, 'h1', 'vault-a', '/docs/renamed.txt')
    with _connect(vault_db) as conn:
        row = conn.execute("SELECT * FROM file_vault WHERE file_hash='h1' AND vault_id='vault-a'").fetchone()
    assert row['file_path'] == os.path.normpath('/docs/renamed.txt')
    assert row['added_at'] == '2024-01-01T00:00:00'   # preserved


# ── Test 9 ─────────────────────────────────────────────────────────────────────

def test_get_vault_paths(vault_db):
    path_b1 = os.path.normpath('Z:/nas/a.txt')
    path_b2 = os.path.normpath('Z:/nas/b.txt')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h2', '/docs/b.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b1,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h2', 'vault-b', ?)", (path_b2,))
        conn.commit()
    paths = manager.get_vault_paths(vault_db, ['h1', 'h2'], 'vault-b')
    assert paths['h1'] == path_b1
    assert paths['h2'] == path_b2
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py -v 2>&1 | head -40
```

Expected: 6 FAILs — `AttributeError` or `OperationalError: no such table: file_vault`

- [ ] **Step 3: Add `file_vault` table and vault_id index to `init_db()` executescript**

In `core/manager.py`, inside the `executescript("""...""")` block (after the `face_detections` table, before the closing `"""`), add:

```sql
            CREATE TABLE IF NOT EXISTS file_vault (
                file_hash TEXT NOT NULL REFERENCES tasks(file_hash),
                vault_id  TEXT NOT NULL REFERENCES vaults(vault_id),
                file_path TEXT NOT NULL,
                added_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (file_hash, vault_id)
            );
            CREATE INDEX IF NOT EXISTS idx_file_vault_vault_id ON file_vault(vault_id);
```

- [ ] **Step 4: Add backfill migration to `init_db()`**

Still inside the `with _connect(db_path) as conn:` block in `init_db()`, just before the final `conn.commit()` at line ~285, add:

```python
        # file_vault backfill — idempotent (INSERT OR IGNORE on PK)
        conn.execute("""
            INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
            SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id IS NOT NULL
        """)
```

- [ ] **Step 5: Add backfill to `bootstrap_default_vault()`**

In `bootstrap_default_vault()`, after line 317 (`"UPDATE tasks SET vault_id = ? WHERE vault_id IS NULL"`) and before `conn.commit()`:

```python
        conn.execute("""
            INSERT OR IGNORE INTO file_vault (file_hash, vault_id, file_path)
            SELECT file_hash, vault_id, file_path FROM tasks WHERE vault_id = ?
        """, (vault_id,))
```

- [ ] **Step 6: Add three new functions to `core/manager.py`**

Add after `insert_child_tasks()` (around line 360):

```python
def upsert_file_vault(db_path, file_hash, vault_id, file_path):
    """Register (or update) a file-vault membership with the vault-specific path.

    Uses ON CONFLICT DO UPDATE so added_at is preserved on re-scan.
    file_path is normalised before storage so ingestor comparisons are stable.
    """
    if not vault_id:
        return
    norm = os.path.normpath(file_path)
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO file_vault (file_hash, vault_id, file_path)
               VALUES (?, ?, ?)
               ON CONFLICT(file_hash, vault_id) DO UPDATE SET file_path = excluded.file_path""",
            (file_hash, vault_id, norm)
        )
        conn.commit()


def get_file_vault_path(db_path, file_hash, vault_id):
    """Return the stored file_path for (file_hash, vault_id), or None if not registered."""
    if not vault_id:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT file_path FROM file_vault WHERE file_hash = ? AND vault_id = ?",
            (file_hash, vault_id),
        ).fetchone()
    return row['file_path'] if row else None


def get_vault_paths(db_path, file_hashes, vault_id):
    """Return {file_hash: file_path} from file_vault for the given vault and hashes.

    Returns {} on any DB error so callers silently keep canonical paths.
    Uses parameterised IN clause — never string-interpolated input.
    """
    if not file_hashes:
        return {}
    try:
        file_hashes = list(file_hashes)
        placeholders = ','.join(['?'] * len(file_hashes))
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT file_hash, file_path FROM file_vault"
                f" WHERE vault_id = ? AND file_hash IN ({placeholders})",
                [vault_id, *file_hashes],
            ).fetchall()
        return {r['file_hash']: r['file_path'] for r in rows}
    except Exception:
        return {}
```

- [ ] **Step 7: Run tests to verify they pass**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py::test_file_vault_table_created tests/test_multi_vault_membership.py::test_backfill_on_migration tests/test_multi_vault_membership.py::test_upsert_file_vault_new tests/test_multi_vault_membership.py::test_upsert_file_vault_existing_task tests/test_multi_vault_membership.py::test_upsert_file_vault_path_update_preserves_added_at tests/test_multi_vault_membership.py::test_get_vault_paths -v
```

Expected: 6 PASSED

- [ ] **Step 8: Run full existing test suite to check for regressions**

```
cd E:\DocVault
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass

- [ ] **Step 9: Commit**

```bash
git add core/manager.py tests/test_multi_vault_membership.py
git commit -m "feat(vault): add file_vault junction table and manager helpers"
```

---

### Task 2: Vault-aware filtering — manager.py + search/fts.py

**Files:**
- Modify: `core/manager.py` (fts_search lines 715–744, _filename_filter_clauses lines 770–785, filename_search line 850, get_filtered_hashes lines 861–883)
- Modify: `search/fts.py` (_regex_search lines 54–92)
- Modify: `tests/test_multi_vault_membership.py`

#### Key context for implementer

`fts_search()` currently adds `tasks.vault_id IN (...)` to the WHERE clause when `vault_ids` is set. This must be **replaced** (not added to) with an INNER JOIN on `file_vault`. Two separate SQL templates are required — one for vault-filtered queries (uses `fv.file_path`), one for unfiltered (uses `fts_index.file_path`).

`_regex_search()` in `search/fts.py` currently adds `tasks.vault_id IN (...)` — same replacement required.

`_filename_filter_clauses()` currently adds `vault_id IN (...)` — replace with a subquery against `file_vault`.

`filename_search()` plain-text branch at line 850 currently passes only 3 args to `_filename_filter_clauses`, omitting `vault_ids` — this pre-existing bug becomes a correctness bug after the helper is updated; fix the call site.

`get_filtered_hashes()` currently queries `tasks` with `vault_id IN (...)` — replace with a query against `file_vault` joined to `tasks`.

- [ ] **Step 1: Add failing tests 6, 7, 8 to the test file**

Append to `tests/test_multi_vault_membership.py`:

```python
# ── Test 6 ─────────────────────────────────────────────────────────────────────

def test_get_filtered_hashes_multi_vault(vault_db):
    # Use os.path.normpath so paths are consistent with what upsert_file_vault stores.
    path_a = os.path.normpath('/docs/a.txt')
    path_b = os.path.normpath('Z:/nas/a.txt')   # Z: prefix keeps normpath stable on Windows
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'txt', 'vault-a')", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b,))
        conn.commit()
    hashes_a = manager.get_filtered_hashes(vault_db, vault_ids=['vault-a'])
    hashes_b = manager.get_filtered_hashes(vault_db, vault_ids=['vault-b'])
    assert 'h1' in hashes_a
    assert 'h1' in hashes_b


# ── Test 7 ─────────────────────────────────────────────────────────────────────

def test_fts_search_vault_filter_returns_vault_path(vault_db):
    path_a = os.path.normpath('/docs/a.txt')
    path_b = os.path.normpath('Z:/nas/a.txt')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'txt', 'vault-a')", (path_a,))
        conn.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES ('h1', 0, ?, 'hello world')", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b,))
        conn.commit()
    results = manager.fts_search(vault_db, 'hello', vault_ids=['vault-b'])
    assert len(results) == 1
    assert results[0]['file_path'] == path_b


# ── Test 8 ─────────────────────────────────────────────────────────────────────

def test_filename_search_vault_filter(vault_db):
    path_photo_a = os.path.normpath('/docs/photo.jpg')
    path_photo_b = os.path.normpath('Z:/nas/photo.jpg')
    path_report  = os.path.normpath('/docs/report.pdf')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'jpg', 'vault-a')", (path_photo_a,))
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h2', ?, 'pdf', 'vault-a')", (path_report,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_photo_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_photo_b,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h2', 'vault-a', ?)", (path_report,))
        conn.commit()
    results = manager.filename_search(vault_db, 'photo', vault_ids=['vault-b'])
    assert len(results) == 1
    assert results[0]['file_hash'] == 'h1'
    results_no_filter = manager.filename_search(vault_db, 'photo')
    assert len(results_no_filter) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py::test_get_filtered_hashes_multi_vault tests/test_multi_vault_membership.py::test_fts_search_vault_filter_returns_vault_path tests/test_multi_vault_membership.py::test_filename_search_vault_filter -v
```

Expected: 3 FAILs

- [ ] **Step 3: Replace `get_filtered_hashes()` in `core/manager.py`**

Replace the entire `get_filtered_hashes` function (lines 861–883):

```python
def get_filtered_hashes(db_path, file_type=None, date_from=None, date_to=None, vault_ids=None):
    """Return list of file_hashes matching constraints, or None if no constraints active."""
    if not any([file_type, date_from, date_to, vault_ids]):
        return None  # no filter — caller should not restrict Qdrant

    if vault_ids:
        # Use file_vault as the membership source; join tasks for attribute filters
        placeholders = ','.join(['?'] * len(vault_ids))
        where = [f"fv.vault_id IN ({placeholders})"]
        params = list(vault_ids)
        if file_type:
            where.append("t.file_type LIKE ?")
            params.append(f"%{file_type.strip()}%")
        if date_from:
            where.append("t.file_modified >= ?")
            params.append(date_from)
        if date_to:
            where.append("t.file_modified <= ?")
            params.append(date_to + "T23:59:59")
        clause = " AND ".join(where)
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"""SELECT DISTINCT fv.file_hash
                    FROM file_vault fv
                    JOIN tasks t ON fv.file_hash = t.file_hash
                    WHERE {clause}""",
                params
            ).fetchall()
        return [r['file_hash'] for r in rows]

    # No vault filter — query tasks directly
    where, params = [], []
    if file_type:
        where.append("file_type LIKE ?")
        params.append(f"%{file_type.strip()}%")
    if date_from:
        where.append("file_modified >= ?")
        params.append(date_from)
    if date_to:
        where.append("file_modified <= ?")
        params.append(date_to + "T23:59:59")
    clause = "WHERE " + " AND ".join(where)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT file_hash FROM tasks {clause}", params
        ).fetchall()
    return [r['file_hash'] for r in rows]
```

- [ ] **Step 4: Replace `fts_search()` in `core/manager.py`**

Replace the entire `fts_search` function (lines 715–744):

```python
def fts_search(db_path, query, limit=20,
               file_type=None, date_from=None, date_to=None, vault_ids=None):
    with _connect(db_path) as conn:
        if vault_ids:
            # Vault-filtered: INNER JOIN file_vault; use fv.file_path in SELECT.
            # The old tasks.vault_id IN (...) clause is replaced by this JOIN.
            # Note: if a file belongs to multiple vaults that are both listed in vault_ids,
            # it will produce one result row per vault per chunk. This is acceptable —
            # the common case is single-vault filtering, and the top_k LIMIT bounds output.
            placeholders = ','.join(['?'] * len(vault_ids))
            where = ["fts_index.content MATCH ?",
                     f"fv.vault_id IN ({placeholders})"]
            params = [query, *vault_ids]
            if file_type:
                where.append("t.file_type LIKE ?")
                params.append(f"%{file_type.strip()}%")
            if date_from:
                where.append("t.file_modified >= ?")
                params.append(date_from)
            if date_to:
                where.append("t.file_modified <= ?")
                params.append(date_to + "T23:59:59")
            clause = " AND ".join(where)
            rows = conn.execute(
                f"""SELECT fts_index.file_hash, fts_index.chunk_index, fv.file_path,
                       fts_index.content AS chunk_text,
                       snippet(fts_index, 3, '<b>', '</b>', '...', 32) AS snippet,
                       fts_index.rank
                   FROM fts_index
                   JOIN tasks t ON fts_index.file_hash = t.file_hash
                   JOIN file_vault fv ON fts_index.file_hash = fv.file_hash
                   WHERE {clause}
                   ORDER BY fts_index.rank LIMIT ?""",
                params + [limit]
            ).fetchall()
        else:
            # No vault filter — original query using fts_index.file_path
            where = ["fts_index.content MATCH ?"]
            params = [query]
            if file_type:
                where.append("tasks.file_type LIKE ?")
                params.append(f"%{file_type.strip()}%")
            if date_from:
                where.append("tasks.file_modified >= ?")
                params.append(date_from)
            if date_to:
                where.append("tasks.file_modified <= ?")
                params.append(date_to + "T23:59:59")
            clause = " AND ".join(where)
            rows = conn.execute(
                f"""SELECT fts_index.file_hash, fts_index.chunk_index, fts_index.file_path,
                   fts_index.content AS chunk_text,
                   snippet(fts_index, 3, '<b>', '</b>', '...', 32) AS snippet,
                   fts_index.rank
                   FROM fts_index
                   JOIN tasks ON fts_index.file_hash = tasks.file_hash
                   WHERE {clause}
                   ORDER BY fts_index.rank LIMIT ?""",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: Replace `_filename_filter_clauses()` and fix call site in `core/manager.py`**

Replace `_filename_filter_clauses()` (lines 770–785):

```python
def _filename_filter_clauses(file_type, date_from, date_to, vault_ids=None):
    """Return (where_fragments, params) for the common filename filter fields."""
    where, params = [], []
    if file_type:
        where.append("LOWER(file_type) = LOWER(?)")
        params.append(file_type.strip().lower())
    if date_from:
        where.append("DATE(COALESCE(file_modified, file_created)) >= ?")
        params.append(date_from)
    if date_to:
        where.append("DATE(COALESCE(file_modified, file_created)) <= ?")
        params.append(date_to)
    if vault_ids:
        placeholders = ','.join(['?'] * len(vault_ids))
        where.append(
            f"file_hash IN (SELECT file_hash FROM file_vault WHERE vault_id IN ({placeholders}))"
        )
        params.extend(vault_ids)
    return where, params
```

Then fix the plain-text branch call site in `filename_search()`. Find this line (around line 850):

```python
        extra_where, extra_params = _filename_filter_clauses(file_type, date_from, date_to)
```

Change it to:

```python
        extra_where, extra_params = _filename_filter_clauses(file_type, date_from, date_to, vault_ids)
```

- [ ] **Step 6: Replace vault filter in `_regex_search()` in `search/fts.py`**

Replace the entire `_regex_search` function (lines 54–92):

```python
def _regex_search(db_path: str, pattern: str, limit: int,
                  file_type: str, date_from: str, date_to: str,
                  vault_ids: list = None) -> list[dict]:
    """Scan fts_index chunks with a Python regex. Full table scan — use sparingly."""
    try:
        re.compile(pattern)  # validate before hitting DB
    except re.error:
        return []

    from core.manager import _connect
    with _connect(db_path) as conn:
        _register_regexp(conn)
        where = ["fts_index.content REGEXP ?"]
        params = [pattern]
        if file_type:
            where.append("tasks.file_type LIKE ?")
            params.append(f"%{file_type.strip()}%")
        if date_from:
            where.append("tasks.file_modified >= ?")
            params.append(date_from)
        if date_to:
            where.append("tasks.file_modified <= ?")
            params.append(date_to + "T23:59:59")

        if vault_ids:
            # Replace tasks.vault_id IN (...) with a file_vault JOIN
            placeholders = ','.join(['?'] * len(vault_ids))
            where.append(f"fv.vault_id IN ({placeholders})")
            params.extend(vault_ids)
            clause = " AND ".join(where)
            rows = conn.execute(
                f"""SELECT fts_index.file_hash, fts_index.chunk_index, fv.file_path,
                           fts_index.content AS chunk_text,
                           NULL AS snippet,
                           0 AS rank
                    FROM fts_index
                    JOIN tasks ON fts_index.file_hash = tasks.file_hash
                    JOIN file_vault fv ON fts_index.file_hash = fv.file_hash
                    WHERE {clause}
                    LIMIT ?""",
                params + [limit]
            ).fetchall()
        else:
            clause = " AND ".join(where)
            rows = conn.execute(
                f"""SELECT fts_index.file_hash, fts_index.chunk_index, fts_index.file_path,
                           fts_index.content AS chunk_text,
                           NULL AS snippet,
                           0 AS rank
                    FROM fts_index
                    JOIN tasks ON fts_index.file_hash = tasks.file_hash
                    WHERE {clause}
                    LIMIT ?""",
                params + [limit]
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 7: Run Task 2 tests**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py::test_get_filtered_hashes_multi_vault tests/test_multi_vault_membership.py::test_fts_search_vault_filter_returns_vault_path tests/test_multi_vault_membership.py::test_filename_search_vault_filter -v
```

Expected: 3 PASSED

- [ ] **Step 8: Run full test suite for regressions**

```
cd E:\DocVault
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass

- [ ] **Step 9: Commit**

```bash
git add core/manager.py search/fts.py tests/test_multi_vault_membership.py
git commit -m "feat(vault): vault-aware filtering via file_vault junction table"
```

---

## Chunk 2: Ingestor and Route Handler Path Substitution

### Task 3: Vault-aware ingestor branching

**Files:**
- Modify: `core/ingestor.py` (file loop lines 112–148, directory task block lines 93–105)
- Modify: `tests/test_multi_vault_membership.py`

#### Key context for implementer

The ingestor currently checks `if existing is None` for new files and `elif path differs` for moves. In a multi-vault setup, a second vault scanning the same file always fails the `existing is None` check and hits the move-detection branch — comparing the NAS path against `tasks.file_path` (origin vault path). This corrupts `tasks.file_path` with the NAS path.

The fix replaces the `if/elif` with a vault-aware three-branch structure:
1. `existing is None` → new file (insert task + upsert file_vault)
2. `existing` but no `file_vault` row for this vault → register membership (upsert file_vault only)
3. `existing` and `file_vault` row exists but path changed → move within vault (upsert file_vault; update tasks only if origin vault)

The `moved` counter should only increment on branch 3.

- [ ] **Step 1: Add failing tests 10 and 12 to the test file**

Append to `tests/test_multi_vault_membership.py`:

```python
# ── Test 10 ────────────────────────────────────────────────────────────────────

def test_ingestor_registers_second_vault(tmp_path):
    """Same content, two vault scan paths → both registered in file_vault."""
    import hashlib
    from unittest.mock import patch
    from core import ingestor

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)

    content = b'shared photo content'
    expected_hash = hashlib.sha256(content).hexdigest()

    dir_a = tmp_path / "vault_a"
    dir_a.mkdir()
    (dir_a / "photo.jpg").write_bytes(content)

    dir_b = tmp_path / "vault_b"
    dir_b.mkdir()
    (dir_b / "photo.jpg").write_bytes(content)

    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'My Docs', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_a),))
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-b', 'NAS Photos', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_b),))
        conn.commit()

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
        ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM file_vault WHERE file_hash=?", (expected_hash,)
        ).fetchall()

    vault_ids_found = {r['vault_id'] for r in rows}
    assert 'vault-a' in vault_ids_found
    assert 'vault-b' in vault_ids_found
    assert len(rows) == 2


# ── Test 12 ────────────────────────────────────────────────────────────────────

def test_ingestor_move_non_origin_vault_does_not_corrupt_tasks_path(tmp_path):
    """When non-origin vault detects a path change, tasks.file_path is NOT updated."""
    import hashlib
    from unittest.mock import patch
    from core import ingestor

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)

    content = b'some content'
    expected_hash = hashlib.sha256(content).hexdigest()

    dir_a = tmp_path / "vault_a"
    dir_a.mkdir()
    (dir_a / "photo.jpg").write_bytes(content)

    dir_b = tmp_path / "vault_b"
    dir_b.mkdir()
    (dir_b / "nas_photo.jpg").write_bytes(content)  # same content, different name

    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'Origin', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_a),))
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-b', 'NAS', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_b),))
        conn.commit()

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
        ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT file_path, vault_id FROM tasks WHERE file_hash=?", (expected_hash,)
        ).fetchone()
        fv_b = conn.execute(
            "SELECT file_path FROM file_vault WHERE file_hash=? AND vault_id='vault-b'",
            (expected_hash,)
        ).fetchone()

    assert 'photo.jpg' in task['file_path']    # origin path unchanged
    assert task['vault_id'] == 'vault-a'
    assert 'nas_photo.jpg' in fv_b['file_path']  # vault-b has its own path
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py::test_ingestor_registers_second_vault tests/test_multi_vault_membership.py::test_ingestor_move_non_origin_vault_does_not_corrupt_tasks_path -v
```

Expected: 2 FAILs

- [ ] **Step 3: Rewrite the ingestor file loop in `core/ingestor.py`**

In `ingestor.py`, find the file loop (Part 2, lines 112–148). Replace the existing `if existing is None / elif` block with the vault-aware version:

```python
            try:
                file_hash = _sha256(file_path)
                existing  = manager.get_task(db_path, file_hash)

                if existing is None:
                    # Brand-new file — create task and register vault membership
                    size, created, modified = _stat(file_path)
                    priority = get_priority(ext)
                    manager.insert_task(
                        db_path, file_hash, file_path, ext, priority,
                        file_size=size, file_created=created, file_modified=modified,
                        vault_id=vault_id,
                    )
                    manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                    added += 1
                    logger.info(f"  Added: {name} (priority: {priority})")

                else:
                    vault_path = manager.get_file_vault_path(db_path, file_hash, vault_id)

                    if vault_path is None:
                        # File known globally but first time this vault sees it
                        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                        logger.info(f"  Registered in vault: {name}")

                    elif os.path.normpath(vault_path) != file_path:
                        # File has moved within this vault — update vault-specific path
                        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                        if existing['vault_id'] == vault_id:
                            # Origin vault: also update canonical path in tasks + Qdrant
                            manager.update_task_path(db_path, file_hash, file_path)
                            _update_qdrant_path(file_hash, file_path)
                            moved += 1
                            logger.info(f"  Moved: {existing['file_path']} → {file_path}")

                    # else: known file at known vault path — nothing to do

            except Exception as e:
                logger.warn(f"  Skipped {name}: {e}")
```

- [ ] **Step 4: Add `upsert_file_vault` to the directory task block in `core/ingestor.py`**

In the folder intelligence block (lines 93–105), move `upsert_file_vault` **outside** the `if not manager.get_task(...)` guard so that it runs unconditionally — both when the task is new and when a second vault finds an already-known directory unit:

```python
                try:
                    if not manager.get_task(db_path, folder_hash):
                        manager.insert_task(
                            db_path, folder_hash, root_norm, f"directory/{ext}", 5,
                            vault_id=vault_id
                        )
                        logger.info(f"  Added Directory Unit: {os.path.basename(root)} (type: {ext})")
                        added += 1
                    manager.upsert_file_vault(db_path, folder_hash, vault_id, root_norm)
                except Exception as e:
                    logger.warn(f"  Skipped directory {os.path.basename(root)}: {e}")
```

`upsert_file_vault` is idempotent (ON CONFLICT DO UPDATE), so running it on every scan is safe.

- [ ] **Step 5: Run ingestor tests**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py::test_ingestor_registers_second_vault tests/test_multi_vault_membership.py::test_ingestor_move_non_origin_vault_does_not_corrupt_tasks_path -v
```

Expected: 2 PASSED

- [ ] **Step 6: Run full test suite for regressions**

```
cd E:\DocVault
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass

- [ ] **Step 7: Commit**

```bash
git add core/ingestor.py tests/test_multi_vault_membership.py
git commit -m "feat(vault): vault-aware ingestor — register multi-vault membership without corrupting origin path"
```

---

### Task 4: Route handler path substitution

**Files:**
- Modify: `api/routes/search.py`
- Modify: `api/routes/query.py`
- Modify: `tests/test_multi_vault_membership.py`

#### Key context for implementer

FTS results already carry the vault-specific path (from the INNER JOIN in Task 2). Semantic and hybrid results carry Qdrant payload paths (canonical origin path). After results come back in the route handlers, apply `manager.get_vault_paths()` to substitute vault-specific paths — but only when exactly one vault is selected (`len(vault_id_list) == 1`).

In `query.py`, path substitution must happen **before** the `sources` list comprehension, since `sources` is built from `results['file_path']`.

In `search.py`, apply substitution:
- semantic mode: after `semantic.async_search()` returns
- hybrid mode: after `hybrid.async_search()` returns (before the `qdrant_offline` fallback check — the fallback goes to FTS which handles paths correctly via the JOIN)

- [ ] **Step 1: Add failing test 11 to the test file**

Append to `tests/test_multi_vault_membership.py`:

```python
# ── Test 11 ────────────────────────────────────────────────────────────────────

def test_multi_vault_filter_no_path_substitution(vault_db):
    """Single-vault: path substituted to vault-specific path.
    Multi-vault: canonical path kept (get_vault_paths not applied)."""
    path_a = os.path.normpath('/docs/a.txt')
    path_b = os.path.normpath('Z:/nas/a.txt')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'txt', 'vault-a')", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b,))
        conn.commit()

    results = [{'file_hash': 'h1', 'file_path': path_a, 'score': 1.0}]

    # Single vault → substitute
    vault_id_list = ['vault-b']
    if vault_id_list and len(vault_id_list) == 1:
        paths = manager.get_vault_paths(vault_db, {r['file_hash'] for r in results}, vault_id_list[0])
        for r in results:
            if r.get('file_hash') in paths:
                r['file_path'] = paths[r['file_hash']]
    assert results[0]['file_path'] == path_b

    # Multi-vault → no substitution
    results = [{'file_hash': 'h1', 'file_path': path_a, 'score': 1.0}]
    vault_id_list = ['vault-a', 'vault-b']
    if vault_id_list and len(vault_id_list) == 1:
        paths = manager.get_vault_paths(vault_db, {r['file_hash'] for r in results}, vault_id_list[0])
        for r in results:
            if r.get('file_hash') in paths:
                r['file_path'] = paths[r['file_hash']]
    assert results[0]['file_path'] == path_a   # canonical unchanged
```

- [ ] **Step 2: Run test to verify it fails**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py::test_multi_vault_filter_no_path_substitution -v
```

Expected: FAIL (because `get_vault_paths` doesn't exist yet in the test's logic... actually this test may PASS already since it's testing logic we implemented in Task 1). If it passes already, note it and move on.

- [ ] **Step 3: Add path substitution helper to `api/routes/search.py`**

In `search.py`, add a helper function after the `_limit` function (before the route handlers):

```python
def _substitute_vault_paths(db, results, vault_id_list):
    """Replace file_path in results with vault-specific path when single vault selected."""
    if not (vault_id_list and len(vault_id_list) == 1 and results):
        return
    paths = manager.get_vault_paths(db, {r.get('file_hash') for r in results if r.get('file_hash')}, vault_id_list[0])
    for r in results:
        if r.get('file_hash') in paths:
            r['file_path'] = paths[r['file_hash']]
```

- [ ] **Step 4: Apply substitution in semantic and hybrid routes in `search.py`**

In the semantic branch, after `results = await semantic.async_search(...)`:

```python
    if mode == 'semantic':
        results = await semantic.async_search(q, top_k=n, hash_filter=hash_filter)
        _substitute_vault_paths(db, results, vault_id_list)
        return JSONResponse(content={'results': results, 'degraded': False})
```

In the hybrid branch, after `results, qdrant_offline = await hybrid.async_search(...)`:

```python
    results, qdrant_offline = await hybrid.async_search(
        db_path=db, query=q, top_k=n,
        file_type=file_type, date_from=date_from, date_to=date_to,
        hash_filter=hash_filter, vault_ids=vault_id_list
    )
    if qdrant_offline:
        fts_results = fts.search(db, q, n,
                                 file_type=file_type, date_from=date_from, date_to=date_to,
                                 vault_ids=vault_id_list)
        return JSONResponse(content={
            'results': fts_results,
            'degraded': True,
            'degraded_reason': 'Qdrant unavailable — showing full-text results only',
        })
    _substitute_vault_paths(db, results, vault_id_list)
    return JSONResponse(content={'results': results, 'degraded': False})
```

- [ ] **Step 5: Apply substitution in `api/routes/query.py`**

First, add `db = get_db()` at the top of the `rag_query` function (after `notify_user_activity()`) so `get_db()` is called only once:

```python
@router.post("/query")
async def rag_query(req: QueryRequest):
    notify_user_activity()
    db = get_db()
    top_k      = req.top_k or int(settings.get('search:rag_top_k') or 5)
    vault_id_list = [v.strip() for v in req.vault_ids.split(',') if v.strip()] if req.vault_ids else None
    ...
    hash_filter = manager.get_filtered_hashes(
        db,
        ...
    )
    ...
    results, _ = await hybrid.async_search(
        db_path=db, ...
    )
```

Then, after `results, _ = await hybrid.async_search(...)` and before the `chunks` / `sources` lines, add:

```python
        # Substitute vault-specific file paths for single-vault RAG queries
        if vault_id_list and len(vault_id_list) == 1 and results:
            _paths = manager.get_vault_paths(
                db, {r.get('file_hash') for r in results if r.get('file_hash')}, vault_id_list[0]
            )
            for r in results:
                if r.get('file_hash') in _paths:
                    r['file_path'] = _paths[r['file_hash']]

        chunks  = [r.get('chunk_text', '') for r in results]
        sources = [{'file_path': r.get('file_path'), 'score': r.get('score'), 'combined_score': r.get('combined_score')}
                   for r in results]
```

- [ ] **Step 6: Run all 12 tests**

```
cd E:\DocVault
pytest tests/test_multi_vault_membership.py -v
```

Expected: 12 PASSED

- [ ] **Step 7: Run full test suite**

```
cd E:\DocVault
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all previously passing tests still pass

- [ ] **Step 8: Commit**

```bash
git add api/routes/search.py api/routes/query.py tests/test_multi_vault_membership.py
git commit -m "feat(vault): substitute vault-specific paths in semantic/hybrid/RAG results"
```

---

## Final verification

- [ ] **Run complete test suite one last time**

```
cd E:\DocVault
pytest tests/ -q 2>&1 | tail -10
```

Expected: all tests pass, 0 failures
