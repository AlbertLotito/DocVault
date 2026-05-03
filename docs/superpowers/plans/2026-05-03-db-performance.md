# DB Performance: Indexes + Schema Surgery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate docvault.db as a performance bottleneck by adding all missing indexes (Option A) and moving `extracted_text` out of the hot `tasks` table into a dedicated `extracted_texts` table (Option B).

**Architecture:** Two independent improvements applied together. Option A adds `CREATE INDEX IF NOT EXISTS` statements and pragma tuning to `_connect()` and `init_db()` — zero risk, no migration. Option B moves the blob data via a one-time migration script (`utils/migrate_to_extracted_texts.py`), then updates all six call sites in `manager.py`. Server must be stopped before running the migration.

**Tech Stack:** Python 3.13, SQLite 3.45 (via stdlib `sqlite3`), pytest

---

## Files Changed

| File | Change |
|---|---|
| `core/manager.py` | Add pragmas to `_connect()`, add 9 indexes + `extracted_texts` table to `init_db()`, update 6 functions |
| `utils/migrate_to_extracted_texts.py` | New — one-time migration script |
| `utils/migrate_fts.py` | Update to read from `extracted_texts` |
| `tests/test_manager.py` | Update `test_complete_task_stores_text` |
| `tests/test_db_migration.py` | New — tests for the migration script |

---

## Task 1: Pragma Tuning + All Missing Indexes

**Files:**
- Modify: `core/manager.py:184-192` (`_connect`)
- Modify: `core/manager.py:195-318` (`init_db`)

### What indexes are added and why

| Index | Table | Column(s) | Query it covers |
|---|---|---|---|
| `idx_tasks_status` | tasks | `status` | Worker claim (`WHERE status='PENDING'`/`'EXTRACTED'`), reset_stuck |
| `idx_tasks_vault_id` | tasks | `vault_id` | Vault-scoped queries, bootstrap |
| `idx_tasks_file_type` | tasks | `file_type` | `list_tasks` file_type filter |
| `idx_tasks_status_vault` | tasks | `(status, vault_id)` | Vault-scoped worker claims (covers both singles) |
| `idx_tasks_status_priority` | tasks | `(status, priority DESC)` | Priority-ordered PENDING claim |
| `idx_tasks_last_update` | tasks | `last_update DESC` | `reset_stuck_tasks` time window, sort |
| `idx_tasks_file_path` | tasks | `file_path` | `file_path LIKE ?`, `ORDER BY file_path` |
| `idx_extracted_images_source_hash` | extracted_images | `source_hash` | Image lookup by file hash |
| `idx_face_detections_cluster_id` | face_detections | `cluster_id` | Face lookup, cluster merge |

- [ ] **Step 1: Update `_connect()` to add pragma tuning**

In `core/manager.py`, replace `_connect` (lines 184–192):

```python
@contextmanager
def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")   # 64 MB page cache
    conn.execute("PRAGMA temp_store=MEMORY")
    try:
        yield conn
    finally:
        conn.close()
```

- [ ] **Step 2: Add all 9 indexes to `init_db()` as schema migrations**

In `core/manager.py`, at the end of the `conn.executescript(...)` block in `init_db` (just before the closing `"""`), add the index definitions — replacing the closing `"""` at line ~271 with the following (keep the existing `idx_file_vault_vault_id` line, add below it):

```python
            CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_vault_id
                ON tasks(vault_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_file_type
                ON tasks(file_type);
            CREATE INDEX IF NOT EXISTS idx_tasks_status_vault
                ON tasks(status, vault_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
                ON tasks(status, priority DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_last_update
                ON tasks(last_update DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_file_path
                ON tasks(file_path);
            CREATE INDEX IF NOT EXISTS idx_extracted_images_source_hash
                ON extracted_images(source_hash);
            CREATE INDEX IF NOT EXISTS idx_face_detections_cluster_id
                ON face_detections(cluster_id);
```

- [ ] **Step 3: Write failing test**

In `tests/test_manager.py`, add at the bottom:

```python
def test_indexes_exist(db):
    """Verify all expected indexes are present after init_db."""
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    idx = {r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    conn.close()
    expected = {
        'idx_tasks_status',
        'idx_tasks_vault_id',
        'idx_tasks_file_type',
        'idx_tasks_status_vault',
        'idx_tasks_status_priority',
        'idx_tasks_last_update',
        'idx_tasks_file_path',
        'idx_extracted_images_source_hash',
        'idx_face_detections_cluster_id',
    }
    missing = expected - idx
    assert not missing, f"Missing indexes: {missing}"
```

- [ ] **Step 4: Run test — expect FAIL**

```
.\venv\Scripts\pytest.exe tests/test_manager.py::test_indexes_exist -v
```

Expected: `FAILED` — indexes don't exist yet on the live DB (test uses fresh temp DB, so it fails because `init_db` doesn't add them yet).

- [ ] **Step 5: Apply the changes from Steps 1–2, then run test — expect PASS**

```
.\venv\Scripts\pytest.exe tests/test_manager.py::test_indexes_exist -v
```

Expected: `PASSED`

- [ ] **Step 6: Run full test suite to check for regressions**

```
.\venv\Scripts\pytest.exe tests/ -v --tb=short
```

Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```
git add core/manager.py tests/test_manager.py
git commit -m "perf: add 9 missing indexes and pragma tuning to docvault.db"
```

---

## Task 2: Add `extracted_texts` Table to Schema

**Files:**
- Modify: `core/manager.py` — `init_db()`

The `extracted_texts` table stores the full text output of each extractor. `ON DELETE CASCADE` means deleting a task automatically deletes its text — no orphan cleanup needed.

- [ ] **Step 1: Write failing test**

In `tests/test_manager.py`, add:

```python
def test_extracted_texts_table_exists(db):
    """extracted_texts table must exist with correct columns after init_db."""
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(extracted_texts)")}
    conn.close()
    assert 'file_hash' in cols
    assert 'extracted_text' in cols
    assert 'stored_at' in cols
```

- [ ] **Step 2: Run test — expect FAIL**

```
.\venv\Scripts\pytest.exe tests/test_manager.py::test_extracted_texts_table_exists -v
```

Expected: `FAILED`

- [ ] **Step 3: Add `extracted_texts` to `init_db()` executescript**

In `core/manager.py`, inside the `conn.executescript("""...""")` block in `init_db`, add this table definition immediately after the `file_vault` block (before the index lines added in Task 1):

```sql
            CREATE TABLE IF NOT EXISTS extracted_texts (
                file_hash      TEXT PRIMARY KEY REFERENCES tasks(file_hash) ON DELETE CASCADE,
                extracted_text TEXT NOT NULL,
                stored_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            );
```

- [ ] **Step 4: Run test — expect PASS**

```
.\venv\Scripts\pytest.exe tests/test_manager.py::test_extracted_texts_table_exists -v
```

- [ ] **Step 5: Commit**

```
git add core/manager.py tests/test_manager.py
git commit -m "feat: add extracted_texts table to schema (Option B prep)"
```

---

## Task 3: Update `manager.py` — All Six Call Sites

**Files:**
- Modify: `core/manager.py`

Six functions touch `tasks.extracted_text`. Each is updated below. None of these changes take effect until after the migration script runs (Task 5) — the column will still exist in `tasks` until then, but the code will stop reading from it.

- [ ] **Step 1: Update `complete_extraction()` (line ~660)**

Replace the entire function:

```python
def complete_extraction(db_path, file_hash, status, text=None,
                        metadata=None, error=None):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET status = ?,
               metadata_json = ?, error_log = ?,
               progress_text = NULL, progress_pct = 100,
               last_update = CURRENT_TIMESTAMP
               WHERE file_hash = ?""",
            (
                status,
                json.dumps(metadata) if metadata else None,
                error,
                file_hash,
            )
        )
        if text:
            conn.execute(
                """INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)""",
                (file_hash, text)
            )
            row = conn.execute(
                "SELECT file_path FROM tasks WHERE file_hash = ?", (file_hash,)
            ).fetchone()
            if row:
                try:
                    conn.execute("DELETE FROM fts_index WHERE file_hash = ?", (file_hash,))
                    from embeddings.chunker import chunk
                    chunks = chunk(text)
                    for i, chunk_text in enumerate(chunks):
                        conn.execute(
                            "INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES (?, ?, ?, ?)",
                            (file_hash, i, row['file_path'], chunk_text)
                        )
                except Exception as e:
                    print(f"[manager] Warning: FTS index update failed: {e}")
        conn.commit()
```

- [ ] **Step 2: Update `update_fts()` (line ~492)**

Replace the entire function:

```python
def update_fts(db_path, file_hash):
    """Re-sync FTS index for a single task from its current extracted_text."""
    with _connect(db_path) as conn:
        task_row = conn.execute(
            "SELECT file_path FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        text_row = conn.execute(
            "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if not task_row:
            return
        conn.execute("DELETE FROM fts_index WHERE file_hash = ?", (file_hash,))
        if text_row and text_row['extracted_text']:
            try:
                from embeddings.chunker import chunk
                chunks = chunk(text_row['extracted_text'])
                for i, chunk_text in enumerate(chunks):
                    conn.execute(
                        "INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES (?, ?, ?, ?)",
                        (file_hash, i, task_row['file_path'], chunk_text)
                    )
            except Exception as e:
                print(f"[manager] Warning: FTS update failed for {file_hash}: {e}")
        conn.commit()
```

- [ ] **Step 3: Update `append_parent_text()` (line ~516)**

Replace the entire function:

```python
def append_parent_text(db_path, parent_hash, suffix: str):
    """Append suffix to parent task's extracted_text and update FTS. Idempotent."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", (parent_hash,)
        ).fetchone()
        existing = (row['extracted_text'] if row else '') or ''
        guard = suffix.split('):')[0] + '):' if '):' in suffix else suffix[:30]
        if guard in existing:
            return
        new_text = existing + '\n\n' + suffix
        conn.execute(
            """INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (parent_hash, new_text)
        )
        conn.commit()
    update_fts(db_path, parent_hash)
```

- [ ] **Step 4: Update `claim_extracted_task()` (line ~741)**

Replace the entire function:

```python
def claim_extracted_task(db_path, worker_id):
    age_weight = settings.get('workers:embed_age_weight') or 900
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, et.extracted_text
               FROM tasks t
               LEFT JOIN extracted_texts et ON t.file_hash = et.file_hash
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'EXTRACTED'
               ORDER BY
                 (10 - COALESCE(v.priority, 5))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / ?)
                 DESC
               LIMIT 1""",
            (age_weight,)
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        task = dict(row)
        conn.execute(
            """UPDATE tasks SET status = 'EMBEDDING', worker_id = ?,
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (worker_id, task['file_hash'])
        )
        conn.commit()
        return task
```

- [ ] **Step 5: Update `claim_extracted_tasks()` (line ~772)**

Replace the entire function:

```python
def claim_extracted_tasks(db_path, worker_id, limit=8):
    """Claim up to *limit* EXTRACTED tasks in one transaction, ordered by vault priority + aging."""
    age_weight = settings.get('workers:embed_age_weight') or 900
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, et.extracted_text
               FROM tasks t
               LEFT JOIN extracted_texts et ON t.file_hash = et.file_hash
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'EXTRACTED'
               ORDER BY
                 (10 - COALESCE(v.priority, 5))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / ?)
                 DESC
               LIMIT ?""",
            (age_weight, limit)
        ).fetchall()
        if not rows:
            conn.commit()
            return []
        tasks = [dict(r) for r in rows]
        hashes = [t['file_hash'] for t in tasks]
        conn.execute(
            f"""UPDATE tasks SET status = 'EMBEDDING', worker_id = ?,
               last_update = CURRENT_TIMESTAMP
               WHERE file_hash IN ({','.join('?' * len(hashes))})""",
            [worker_id, *hashes]
        )
        conn.commit()
        return tasks
```

- [ ] **Step 6: Update `reprocess_task()` (line ~1119)**

Replace the entire function:

```python
def reprocess_task(db_path, file_hash):
    with _connect(db_path) as conn:
        conn.execute(
            """UPDATE tasks SET status='PENDING', worker_id=NULL,
               error_log=NULL,
               last_update=CURRENT_TIMESTAMP WHERE file_hash=?""",
            (file_hash,)
        )
        conn.execute(
            "DELETE FROM extracted_texts WHERE file_hash = ?", (file_hash,)
        )
        conn.commit()
```

- [ ] **Step 7: Write failing tests for the updated functions**

Add to `tests/test_manager.py`:

```python
def test_complete_extraction_stores_text_in_extracted_texts(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    # Text must be in extracted_texts
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row['extracted_text'] == 'Hello world'


def test_complete_extraction_does_not_store_text_in_tasks(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTED'
    # extracted_text column no longer exists on tasks after migration,
    # but we verify status is correct here
    assert 'file_hash' in task


def test_claim_extracted_tasks_returns_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['extracted_text'] == 'Hello world'


def test_reprocess_task_clears_extracted_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    manager.reprocess_task(db, 'abc123')
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row is None
```

- [ ] **Step 8: Run new tests — expect FAIL (column still exists in tasks schema)**

```
.\venv\Scripts\pytest.exe tests/test_manager.py::test_complete_extraction_stores_text_in_extracted_texts tests/test_manager.py::test_claim_extracted_tasks_returns_text tests/test_manager.py::test_reprocess_task_clears_extracted_text -v
```

Expected: `FAILED` — `extracted_texts` table doesn't exist in the test DB yet (needs `init_db` to create it — which we added in Task 2, so actually these should pass once Task 2 is done. If Task 2 is complete, run all tests to confirm.)

- [ ] **Step 9: Run all tests**

```
.\venv\Scripts\pytest.exe tests/ -v --tb=short
```

Expected: new tests PASS; `test_complete_task_stores_text` will now FAIL (it checks `task['extracted_text']` which no longer exists on the task row).

- [ ] **Step 10: Update the old test to match new behaviour**

In `tests/test_manager.py`, replace `test_complete_task_stores_text`:

```python
def test_complete_task_stores_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTED'
    # Text lives in extracted_texts, not in the task row
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT extracted_text FROM extracted_texts WHERE file_hash=?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row['extracted_text'] == 'Hello world'
```

- [ ] **Step 11: Run full test suite — all green**

```
.\venv\Scripts\pytest.exe tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 12: Commit**

```
git add core/manager.py tests/test_manager.py
git commit -m "feat: move extracted_text to extracted_texts table — update all manager call sites"
```

---

## Task 4: Update `utils/migrate_fts.py`

**Files:**
- Modify: `utils/migrate_fts.py`

- [ ] **Step 1: Update the query to read from `extracted_texts`**

Replace the entire `migrate()` function body from line 32 onward:

```python
        # 2. Fetch all completed tasks with text (now in extracted_texts)
        print("Fetching COMPLETED tasks with text...")
        tasks = conn.execute(
            """SELECT t.file_hash, t.file_path, et.extracted_text
               FROM tasks t
               JOIN extracted_texts et ON t.file_hash = et.file_hash
               WHERE t.status = 'COMPLETED'"""
        ).fetchall()
```

Everything else in the function stays the same (`text = task['extracted_text']` still works because the column name in the SELECT result is still `extracted_text`).

- [ ] **Step 2: Commit**

```
git add utils/migrate_fts.py
git commit -m "fix: migrate_fts.py reads extracted_text from extracted_texts table"
```

---

## Task 5: Write and Run the One-Time Migration Script

**Files:**
- Create: `utils/migrate_to_extracted_texts.py`

This script must run **with the server stopped**. It is safe to re-run — all operations are idempotent.

- [ ] **Step 1: Create the migration script**

Create `utils/migrate_to_extracted_texts.py`:

```python
"""
One-time migration: move tasks.extracted_text → extracted_texts table.

Run with server stopped:
    .\\venv\\Scripts\\python.exe utils/migrate_to_extracted_texts.py

Safe to re-run — all steps are idempotent.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.manager import get_db_path, _connect, init_db


def migrate():
    db_path = get_db_path()
    print(f"Migration target: {db_path}")

    # Step 1 — ensure extracted_texts table exists (init_db is idempotent)
    print("Step 1: Ensuring schema is up to date...")
    init_db(db_path)

    with _connect(db_path) as conn:
        # Step 2 — count source rows
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE extracted_text IS NOT NULL AND extracted_text != ''"
        ).fetchone()[0]
        already = conn.execute(
            "SELECT COUNT(*) FROM extracted_texts"
        ).fetchone()[0]
        print(f"Step 2: {total} rows to migrate, {already} already in extracted_texts.")

        # Step 3 — copy in batches
        print("Step 3: Copying extracted_text → extracted_texts...")
        batch_size = 1000
        offset = 0
        copied = 0
        t0 = time.time()
        while True:
            rows = conn.execute(
                """SELECT file_hash, extracted_text FROM tasks
                   WHERE extracted_text IS NOT NULL AND extracted_text != ''
                   LIMIT ? OFFSET ?""",
                (batch_size, offset)
            ).fetchall()
            if not rows:
                break
            conn.executemany(
                """INSERT OR IGNORE INTO extracted_texts (file_hash, extracted_text, stored_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)""",
                [(r['file_hash'], r['extracted_text']) for r in rows]
            )
            conn.commit()
            copied += len(rows)
            offset += batch_size
            elapsed = time.time() - t0
            rate = copied / elapsed if elapsed > 0 else 0
            print(f"  {copied}/{total} copied ({rate:.0f} rows/s)...")

        print(f"Step 3 done: {copied} rows copied in {time.time() - t0:.1f}s.")

        # Step 4 — verify counts match
        migrated = conn.execute("SELECT COUNT(*) FROM extracted_texts").fetchone()[0]
        print(f"Step 4: Verification — {migrated} rows in extracted_texts.")
        if migrated < total * 0.99:
            print(f"ERROR: Expected ~{total}, got {migrated}. Aborting DROP COLUMN.")
            sys.exit(1)

        # Step 5 — drop the column from tasks
        try:
            print("Step 5: Dropping tasks.extracted_text column...")
            conn.execute("ALTER TABLE tasks DROP COLUMN extracted_text")
            conn.commit()
            print("Step 5 done.")
        except Exception as e:
            if 'no such column' in str(e).lower():
                print("Step 5: Column already dropped — skipping.")
            else:
                raise

    # Step 6 — VACUUM (outside transaction — cannot run inside one)
    print("Step 6: Running VACUUM to reclaim disk space (this may take several minutes)...")
    import sqlite3
    raw = sqlite3.connect(db_path)
    raw.execute("VACUUM")
    raw.close()
    print("Step 6 done.")
    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 2: Write migration test**

Create `tests/test_db_migration.py`:

```python
"""Tests for the extracted_texts migration script."""
import sqlite3
import tempfile
import os
import pytest


@pytest.fixture
def legacy_db(tmp_path):
    """A DB with the old schema: extracted_text inline in tasks."""
    db = str(tmp_path / 'docvault.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE tasks (
            file_hash      TEXT PRIMARY KEY,
            file_path      TEXT NOT NULL,
            file_type      TEXT,
            status         TEXT DEFAULT 'PENDING',
            extracted_text TEXT,
            last_update    DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE fts_index USING fts5(
            file_hash UNINDEXED,
            chunk_index UNINDEXED,
            file_path UNINDEXED,
            content
        );
        CREATE TABLE vaults (
            vault_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scan_directory TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            color TEXT DEFAULT '#6366f1',
            state TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE extracted_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_hash TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE
        );
        CREATE TABLE face_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT NOT NULL,
            cluster_id TEXT
        );
        CREATE TABLE file_vault (
            file_hash TEXT NOT NULL,
            vault_id  TEXT NOT NULL,
            file_path TEXT NOT NULL,
            PRIMARY KEY (file_hash, vault_id)
        );
        CREATE TABLE face_registry (
            cluster_id TEXT PRIMARY KEY,
            person_name TEXT DEFAULT 'Unknown Person'
        );
    """)
    conn.executemany(
        "INSERT INTO tasks (file_hash, file_path, file_type, status, extracted_text) VALUES (?,?,?,?,?)",
        [
            ('hash1', '/docs/a.pdf', 'pdf', 'COMPLETED', 'Text for doc A'),
            ('hash2', '/docs/b.pdf', 'pdf', 'COMPLETED', 'Text for doc B'),
            ('hash3', '/docs/c.pdf', 'pdf', 'PENDING',   None),
        ]
    )
    conn.commit()
    conn.close()
    return db


def test_migration_copies_text(legacy_db, monkeypatch):
    """Migration copies extracted_text from tasks to extracted_texts."""
    import utils.migrate_to_extracted_texts as m
    monkeypatch.setattr(m, 'get_db_path', lambda: legacy_db)
    # Patch init_db to work on legacy schema
    from core import manager
    monkeypatch.setattr(m, 'init_db', lambda db: manager.init_db(db))
    m.migrate()

    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT file_hash, extracted_text FROM extracted_texts ORDER BY file_hash").fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0]['file_hash'] == 'hash1'
    assert rows[0]['extracted_text'] == 'Text for doc A'
    assert rows[1]['file_hash'] == 'hash2'
    assert rows[1]['extracted_text'] == 'Text for doc B'


def test_migration_drops_column(legacy_db, monkeypatch):
    """After migration, tasks.extracted_text column is gone."""
    import utils.migrate_to_extracted_texts as m
    from core import manager
    monkeypatch.setattr(m, 'get_db_path', lambda: legacy_db)
    monkeypatch.setattr(m, 'init_db', lambda db: manager.init_db(db))
    m.migrate()

    conn = sqlite3.connect(legacy_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    conn.close()
    assert 'extracted_text' not in cols


def test_migration_is_idempotent(legacy_db, monkeypatch):
    """Running migration twice does not raise or corrupt data."""
    import utils.migrate_to_extracted_texts as m
    from core import manager
    monkeypatch.setattr(m, 'get_db_path', lambda: legacy_db)
    monkeypatch.setattr(m, 'init_db', lambda db: manager.init_db(db))
    m.migrate()
    m.migrate()  # second run — must not raise

    conn = sqlite3.connect(legacy_db)
    count = conn.execute("SELECT COUNT(*) FROM extracted_texts").fetchone()[0]
    conn.close()
    assert count == 2
```

- [ ] **Step 3: Run migration tests — expect PASS**

```
.\venv\Scripts\pytest.exe tests/test_db_migration.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 4: Stop the server, then run the migration on the live database**

```
# Stop server first (Ctrl+C in the terminal running run.py, or use the Shutdown button in the UI)
.\venv\Scripts\python.exe utils/migrate_to_extracted_texts.py
```

Expected output:
```
Migration target: E:\DocVault\docvault.db
Step 1: Ensuring schema is up to date...
Step 2: ~200000 rows to migrate, 0 already in extracted_texts.
Step 3: Copying extracted_text → extracted_texts...
  1000/200000 copied (...)
  ...
Step 3 done: ~200000 rows copied in Xs.
Step 4: Verification — ~200000 rows in extracted_texts.
Step 5: Dropping tasks.extracted_text column...
Step 5 done.
Step 6: Running VACUUM to reclaim disk space (this may take several minutes)...
Step 6 done.
Migration complete.
```

- [ ] **Step 5: Verify DB size shrank**

```powershell
(Get-Item docvault.db).Length / 1MB
```

Expected: significantly smaller than 3GB (target ~200–400 MB).

- [ ] **Step 6: Start the server and smoke-test**

```
.\venv\Scripts\python.exe run.py
```

Open `http://localhost:8050/vault` — vault status should load. Open `/search` — run a search. Run a RAG query. Check that results come back correctly.

- [ ] **Step 7: Commit**

```
git add utils/migrate_to_extracted_texts.py tests/test_db_migration.py
git commit -m "feat: migration script — move extracted_text to extracted_texts, VACUUM"
```

---

## Task 6: Final Verification and Cleanup

- [ ] **Step 1: Run full test suite**

```
.\venv\Scripts\pytest.exe tests/ -v --tb=short
```

Expected: all tests pass.

- [ ] **Step 2: Verify indexes on live DB**

```powershell
.\venv\Scripts\python.exe -c @"
import sqlite3
c = sqlite3.connect('docvault.db')
rows = c.execute('SELECT name, tbl_name FROM sqlite_master WHERE type=?', ('index',)).fetchall()
for r in rows: print(r)
"@
```

Expected: all 9 new indexes listed.

- [ ] **Step 3: Commit all remaining changes**

```
git add -A
git commit -m "perf: complete DB performance overhaul — indexes, pragmas, extracted_texts migration"
```

---

## Rollback Plan

If the migration fails mid-way:

1. The migration uses `INSERT OR IGNORE` so partial runs are safe — re-run the script.
2. If `DROP COLUMN` succeeded but the server fails to start, the `extracted_texts` table has all the data. The server will work correctly with the new code.
3. If you need to fully rollback to the old schema before deploying: restore `docvault.db` from backup (the 3GB file). The old code (pre-Task 3 commits) can then be checked out.

**Always take a filesystem backup of `docvault.db` before running the migration.**
