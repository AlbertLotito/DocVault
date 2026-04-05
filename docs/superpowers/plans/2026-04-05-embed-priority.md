# Priority-Aware Embedding + Tunable Aging Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the embedding worker respect vault priority with aging, and make the age divisor tunable for both extraction and embedding workers via two new settings.

**Architecture:** Two new settings (`workers:extract_age_weight`, `workers:embed_age_weight`) are added to `core/settings.py`. `core/manager.py` gains a `from core.settings import settings` import (currently absent), then `claim_pending_task` replaces its hardcoded `/ 3600.0` with the tunable setting, and `claim_extracted_task`/`claim_extracted_tasks` add a LEFT JOIN on vaults and a vault-priority + aging ORDER BY.

**Tech Stack:** Python, SQLite (julianday arithmetic), FastAPI settings schema (auto-renders in Settings UI)

**Spec:** `E:\DocVault\docs\superpowers\specs\2026-04-05-embed-priority-design.md`

---

## File Map

| File | Change |
|---|---|
| `core/settings.py` | Add `workers:extract_age_weight` and `workers:embed_age_weight` to schema |
| `core/manager.py` | Add `from core.settings import settings`; update `claim_pending_task`, `claim_extracted_task`, `claim_extracted_tasks` |
| `tests/test_embed_priority.py` | 4 new tests |

---

## Chunk 1: Settings + Extraction Age Weight

### Task 1: New Settings + claim_pending_task

**Files:**
- Modify: `core/settings.py` (after `workers:embed_concurrency`, around line 135)
- Modify: `core/manager.py` (line 1–6 for import; lines 696–725 for claim_pending_task)
- Create: `tests/test_embed_priority.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_embed_priority.py`:

```python
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def priority_db(tmp_path):
    """DB with two vaults: vault-hi (priority 1) and vault-lo (priority 9)."""
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-hi', 'High Priority', '/hi', 1, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-lo', 'Low Priority', '/lo', 9, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


def _insert_task(conn, file_hash, vault_id, status, last_update=None, file_path=None):
    """Helper: insert a minimal task row with controllable last_update."""
    if last_update is None:
        last_update = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    if file_path is None:
        file_path = f'/{vault_id}/{file_hash}.txt'
    conn.execute(
        """INSERT INTO tasks (file_hash, file_path, file_type, status, vault_id, last_update)
           VALUES (?, ?, 'txt', ?, ?, ?)""",
        (file_hash, file_path, status, vault_id, last_update)
    )


def test_extract_age_weight_respected(priority_db):
    """claim_pending_task respects workers:extract_age_weight.

    claim_pending_task formula: (10 - vault_priority) * COALESCE(t.priority, 10) + age_bonus
    where age_bonus = seconds_old / age_weight.

    vault-hi (priority 1, fresh):    (10-1)*10 + 0   = 90
    vault-lo (priority 9, 100s old): (10-9)*10 + 100 = 110  → wins when age_weight=1
    """
    old_time = (datetime.utcnow() - timedelta(seconds=100)).strftime('%Y-%m-%d %H:%M:%S')
    with _connect(priority_db) as conn:
        _insert_task(conn, 'hash-hi', 'vault-hi', 'PENDING')        # fresh, score = 90
        _insert_task(conn, 'hash-lo', 'vault-lo', 'PENDING', last_update=old_time)  # old, score = 110
        conn.commit()

    with patch('core.manager.settings') as mock_settings:
        mock_settings.get = lambda key: {'workers:extract_age_weight': 1}.get(key, 3600)
        task = manager.claim_pending_task(priority_db, 'worker-1')

    assert task['file_hash'] == 'hash-lo', "Old low-priority task should overtake fresh high-priority task when age_weight=1"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_embed_priority.py::test_extract_age_weight_respected -v
```

Expected: FAIL — `AttributeError: module 'core.manager' has no attribute 'settings'`
(because the import doesn't exist yet)

- [ ] **Step 3: Add settings to `core/settings.py`**

Find the `workers:embed_concurrency` entry (around line 131). Add the two new entries immediately after it, before the closing `},` of that block ends the worker throughput section:

```python
            'workers:extract_age_weight': {
                'type': 'int', 'default': 3600,
                'label': 'Extraction age weight (s per priority point)', 'group': 'embeddings',
                'description': 'Seconds a PENDING task must wait to gain 1 priority point via aging. '
                               'Lower = ages faster. Default 3600 = 1 hour per point.',
            },
            'workers:embed_age_weight': {
                'type': 'int', 'default': 900,
                'label': 'Embedding age weight (s per priority point)', 'group': 'embeddings',
                'description': 'Seconds an EXTRACTED task must wait to gain 1 priority point via aging. '
                               'Lower = ages faster. Default 900 = 15 minutes per point.',
            },
```

- [ ] **Step 4: Add `settings` import to `core/manager.py`**

The file currently imports only stdlib modules (lines 1–5). Add the settings import after the existing imports:

```python
from core.settings import settings
```

Place it after `from contextlib import contextmanager` (line 5), leaving a blank line between stdlib and local imports:

```python
import re
import sqlite3
import json
import os
from contextlib import contextmanager

from core.settings import settings
```

- [ ] **Step 5: Update `claim_pending_task` in `core/manager.py`**

The function currently starts at line ~688. Add the `age_weight` read and replace the hardcoded `/ 3600.0` with `/ ?` bound parameter.

**Find** (inside `claim_pending_task`):

```python
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.priority, t.vault_id,
                      t.parent_hash, t.metadata_json,
                      COALESCE(v.priority, 5) AS vault_priority
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'PENDING'
               ORDER BY
                 -- Lower vault_priority = more important -> invert with (10 - vault_priority)
                 ((10 - COALESCE(v.priority, 5)) * COALESCE(t.priority, 10))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / 3600.0)
                 DESC
               LIMIT 1"""
        ).fetchone()
```

**Replace with:**

```python
    age_weight = settings.get('workers:extract_age_weight') or 3600
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.priority, t.vault_id,
                      t.parent_hash, t.metadata_json,
                      COALESCE(v.priority, 5) AS vault_priority
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'PENDING'
               ORDER BY
                 -- Lower vault_priority = more important -> invert with (10 - vault_priority)
                 ((10 - COALESCE(v.priority, 5)) * COALESCE(t.priority, 10))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / ?)
                 DESC
               LIMIT 1""",
            (age_weight,)
        ).fetchone()
```

- [ ] **Step 6: Run test to verify it passes**

```
pytest tests/test_embed_priority.py::test_extract_age_weight_respected -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add core/settings.py core/manager.py tests/test_embed_priority.py
git commit -m "feat(workers): add tunable age weights; extract_age_weight applied to claim_pending_task"
```

---

## Chunk 2: Embedding Claim Priority

### Task 2: claim_extracted_task + claim_extracted_tasks + Tests 1–3

**Files:**
- Modify: `core/manager.py` (lines ~728–773: `claim_extracted_task` and `claim_extracted_tasks`)
- Modify: `tests/test_embed_priority.py` (append 3 more tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_embed_priority.py`:

```python
def test_embed_priority_high_vault_first(priority_db):
    """task from vault-hi (priority 1) is claimed before task from vault-lo (priority 9)
    when both have the same last_update (no aging effect)."""
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _connect(priority_db) as conn:
        _insert_task(conn, 'hash-hi', 'vault-hi', 'EXTRACTED', last_update=now)
        _insert_task(conn, 'hash-lo', 'vault-lo', 'EXTRACTED', last_update=now)
        conn.commit()

    tasks = manager.claim_extracted_tasks(priority_db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['file_hash'] == 'hash-hi', "High-priority vault task should be claimed first"


def test_embed_priority_aging_overtakes(priority_db):
    """A vault-lo task aged 3 hours overtakes a fresh vault-hi task.

    vault-hi: score = (10-1) + 0 = 9
    vault-lo: score = (10-9) + (10800 / 900) = 1 + 12 = 13  → wins
    """
    old_time = (datetime.utcnow() - timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    with _connect(priority_db) as conn:
        _insert_task(conn, 'hash-hi', 'vault-hi', 'EXTRACTED', last_update=now)
        _insert_task(conn, 'hash-lo', 'vault-lo', 'EXTRACTED', last_update=old_time)
        conn.commit()

    tasks = manager.claim_extracted_tasks(priority_db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['file_hash'] == 'hash-lo', "3-hour-old low-priority task should overtake fresh high-priority task"


def test_embed_priority_orphaned_task(db):
    """A task with a vault_id that has no matching vaults row is claimed without error.
    COALESCE(v.priority, 5) gives it a neutral score of 5."""
    with _connect(db) as conn:
        _insert_task(conn, 'hash-orphan', 'no-such-vault', 'EXTRACTED')
        conn.commit()

    tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['file_hash'] == 'hash-orphan'
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_embed_priority.py::test_embed_priority_high_vault_first tests/test_embed_priority.py::test_embed_priority_aging_overtakes tests/test_embed_priority.py::test_embed_priority_orphaned_task -v
```

Expected: FAIL — all three fail because `claim_extracted_tasks` still uses `ORDER BY last_update` with no vault JOIN.

- [ ] **Step 3: Update `claim_extracted_task` in `core/manager.py`**

**Find** (full function body, lines ~728–745):

```python
def claim_extracted_task(db_path, worker_id):
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT file_hash, file_path, file_type, extracted_text
               FROM tasks WHERE status = 'EXTRACTED' ORDER BY last_update LIMIT 1"""
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

**Replace with:**

```python
def claim_extracted_task(db_path, worker_id):
    age_weight = settings.get('workers:embed_age_weight') or 900
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.extracted_text
               FROM tasks t
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

- [ ] **Step 4: Update `claim_extracted_tasks` in `core/manager.py`**

**Find** (full function body, lines ~748–773):

```python
def claim_extracted_tasks(db_path, worker_id, limit=8):
    """Claim up to *limit* EXTRACTED tasks in one transaction.

    Returns a list of task dicts (same shape as claim_extracted_task).
    Returns [] when the queue is empty.
    """
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT file_hash, file_path, file_type, extracted_text
               FROM tasks WHERE status = 'EXTRACTED' ORDER BY last_update LIMIT ?""",
            (limit,)
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

**Replace with:**

```python
def claim_extracted_tasks(db_path, worker_id, limit=8):
    """Claim up to *limit* EXTRACTED tasks in one transaction, ordered by vault priority + aging.

    Returns a list of task dicts (same shape as claim_extracted_task).
    Returns [] when the queue is empty.
    """
    age_weight = settings.get('workers:embed_age_weight') or 900
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.extracted_text
               FROM tasks t
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

- [ ] **Step 5: Run all 4 tests to verify they pass**

```
pytest tests/test_embed_priority.py -v
```

Expected: 4 PASS

- [ ] **Step 6: Run full test suite for regressions**

```
pytest tests/ -q --tb=short --ignore=tests/test_router.py --ignore=tests/test_video_extractor.py
```

Expected: same pass/fail counts as before this feature (23 pre-existing failures unrelated to this work).

- [ ] **Step 7: Commit**

```bash
git add core/manager.py tests/test_embed_priority.py
git commit -m "feat(workers): embed claim respects vault priority + aging via embed_age_weight"
```
