# Deleted-File Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when a previously-known file disappears from a vault's scan directory, soft-flag it (`tasks.status = 'MISSING'`) without destroying any extracted work, exclude it from search results, and give the user a UI panel to review and permanently purge confirmed deletions.

**Architecture:** A new `miss_count` column on `file_vault` tracks per-(hash, vault) absence across scan cycles; a new `MISSING` status value on `tasks` (plus `pre_missing_status` for restore) is the single source of truth every other layer (workers, search, UI) already knows how to filter on, since worker claim queries already whitelist specific statuses. Detection lives entirely in `core/ingestor.py::ingest()`. Search exclusion is SQL-side for FTS (cheap, plain SQLite) and a Python post-filter for semantic search (deliberately avoiding LanceDB hash-filter queries, which caused a real 30s+ stall in this codebase before, per `common-bugs.md`). Purging reuses a helper extracted from `VaultManager._gut_vault`'s existing batch-delete logic rather than duplicating it.

**Tech Stack:** Python 3.13, pytest, SQLite, FastAPI, vanilla JS (LCARS UI, no framework).

## Global Constraints

- `file_vault` gets `miss_count INTEGER DEFAULT 0`; `tasks` gets `pre_missing_status TEXT`. Both via idempotent `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`, matching the existing migration pattern in `core/manager.py::init_db`.
- New setting `ingestion:missing_after_scans`, group `ingestion`, type `int`, default `3`.
- A task only flips to `MISSING` when **every** `file_vault` row for its `file_hash` (across all vaults) has `miss_count >= threshold` — multi-vault content must stay visible if any copy still exists (design spec §5).
- Restore mapping when a file reappears: `PROCESSING → PENDING`, `EMBEDDING → EXTRACTED`, everything else restores verbatim (design spec §6).
- Semantic-search exclusion must NOT build a LanceDB hash filter/`IN` clause. Fetch the (expected-small) `MISSING` hash set separately and post-filter in Python (design spec §9). This is a hard constraint, not a style preference — it's avoiding a previously-fixed performance regression.
- Nothing in this feature deletes data without an explicit user action on the new Missing Files panel (design spec §3 non-goals). `VaultManager._gut_vault`'s existing behavior and call sites must not change, beyond delegating its batch-delete internals to the new shared helper.
- Vault Log / catalog browsing (`list_tasks`) is left unchanged — `MISSING` is just another visible status there (design spec §9).

---

### Task 1: Schema migration + `ingestion:missing_after_scans` setting

**Files:**
- Modify: `core/manager.py:324-342` (tasks migration block in `init_db`)
- Modify: `core/settings.py:381-386` (insert new setting after `ingestion:ignore_folders`)
- Test: `tests/test_multi_vault_membership.py` (new test using the existing `vault_db` fixture pattern in that file)

**Interfaces:**
- Produces: `tasks.pre_missing_status` (TEXT, nullable) and `file_vault.miss_count` (INTEGER, default 0) columns, present after `init_db()` runs. `settings.get('ingestion:missing_after_scans')` returns `'3'` by default (string, per this app's `Settings.get()` convention — callers cast with `int(...)`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_multi_vault_membership.py`:

```python
def test_missing_detection_schema_columns_exist(db):
    with _connect(db) as conn:
        fv_cols = [r['name'] for r in conn.execute("PRAGMA table_info(file_vault)").fetchall()]
        task_cols = [r['name'] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    assert 'miss_count' in fv_cols
    assert 'pre_missing_status' in task_cols


def test_missing_after_scans_setting_default():
    from core.settings import settings
    assert int(settings.get('ingestion:missing_after_scans')) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_multi_vault_membership.py::test_missing_detection_schema_columns_exist tests/test_multi_vault_membership.py::test_missing_after_scans_setting_default -v`

Expected: both FAIL — `test_missing_detection_schema_columns_exist` because `miss_count`/`pre_missing_status` don't exist yet; `test_missing_after_scans_setting_default` because the setting key doesn't exist in the schema (`settings.get()` returns `None`, and `int(None)` raises `TypeError`).

- [ ] **Step 3: Add the schema migration**

In `core/manager.py`, inside `init_db()`, immediately after the existing `if 'parent_hash' not in columns:` block (currently lines 340-342):

```python
        if 'parent_hash' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN parent_hash TEXT REFERENCES tasks(file_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent_hash ON tasks(parent_hash)")
        if 'pre_missing_status' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN pre_missing_status TEXT")

        # file_vault migration
        cursor = conn.execute("PRAGMA table_info(file_vault)")
        fv_columns = [row['name'] for row in cursor.fetchall()]
        if 'miss_count' not in fv_columns:
            conn.execute("ALTER TABLE file_vault ADD COLUMN miss_count INTEGER DEFAULT 0")
```

(The first three lines shown — `if 'parent_hash' not in columns:` through the `CREATE INDEX` line — already exist; only the `pre_missing_status` block and the new `file_vault migration` block are additions.)

- [ ] **Step 4: Add the setting**

In `core/settings.py`, insert immediately after the `ingestion:ignore_folders` entry (currently lines 381-386) and before the `# System` comment:

```python
            'ingestion:missing_after_scans': {
                'type': 'int', 'default': 3, 'label': 'Missing after N scans', 'group': 'ingestion',
                'description': 'A file must be absent for this many consecutive vault scans before it is '
                               'flagged MISSING. Protects against transient issues (network drive hiccups, '
                               'drive-letter changes) causing a false flag. Default: 3 (~3 minutes at the '
                               'default 60s scan interval).',
            },
```

- [ ] **Step 5: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_multi_vault_membership.py -v`

Expected: all tests in the file PASS, including the two new ones and every pre-existing test (unaffected — both migrations are additive `ALTER TABLE ADD COLUMN` with defaults).

- [ ] **Step 6: Commit**

```bash
git add core/manager.py core/settings.py tests/test_multi_vault_membership.py
git commit -m "$(cat <<'EOF'
feat: add schema + setting for deleted-file detection

Adds file_vault.miss_count and tasks.pre_missing_status columns
(idempotent ALTER TABLE, matching the existing migration pattern)
and a new ingestion:missing_after_scans setting (default 3). Pure
schema/settings groundwork -- no detection logic yet.
EOF
)"
```

---

### Task 2: Detection & restore logic in `core/ingestor.py`

**Files:**
- Modify: `core/ingestor.py` (the per-file loop in `ingest()`, plus a new `_update_missing_flags` helper)
- Modify: `core/manager.py` (five new functions)
- Test: `tests/test_ingestor.py` (new tests using real vault fixtures, following `tests/test_multi_vault_membership.py`'s `ingestor.ingest(..., vault_id=...)` pattern)

**Interfaces:**
- Consumes: `settings.get('ingestion:missing_after_scans')` (Task 1), `tasks.pre_missing_status` / `file_vault.miss_count` columns (Task 1).
- Produces (new `core/manager.py` functions, consumed by this task's own `ingestor.py` code — no other task depends on these):
  - `get_file_vault_rows(db_path: str, vault_id: str) -> list[dict]` — each dict has `file_hash`, `file_path`, `miss_count`.
  - `set_file_vault_miss_count(db_path: str, file_hash: str, vault_id: str, count: int) -> None`
  - `all_file_vault_rows_missing(db_path: str, file_hash: str, threshold: int) -> bool`
  - `flag_task_missing(db_path: str, file_hash: str) -> None`
  - `restore_task_from_missing(db_path: str, file_hash: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingestor.py`:

```python
def _make_vault(db_path, vault_id, scan_directory):
    from core.manager import _connect
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
               VALUES (?, ?, ?, 5, 'active', '2024-01-01', '2024-01-01')""",
            (vault_id, vault_id, scan_directory)
        )
        conn.commit()


def test_deleted_file_flags_missing_after_threshold(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "gone.txt"
    f.write_text("hello")
    _make_vault(db_path, 'vault-a', str(docs))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute("SELECT file_hash, status FROM tasks").fetchone()
    file_hash = task['file_hash']
    assert task['status'] == 'PENDING'

    f.unlink()
    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        for _ in range(3):  # default threshold
            ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT status, pre_missing_status FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    assert task['status'] == 'MISSING'
    assert task['pre_missing_status'] == 'PENDING'


def test_deleted_file_not_flagged_before_threshold(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "gone.txt"
    f.write_text("hello")
    _make_vault(db_path, 'vault-a', str(docs))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    f.unlink()
    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        for _ in range(2):  # one short of default threshold (3)
            ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute("SELECT status FROM tasks").fetchone()
    assert task['status'] != 'MISSING'


def test_missing_file_restores_on_reappearance(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    f = docs / "gone.txt"
    f.write_text("hello")
    _make_vault(db_path, 'vault-a', str(docs))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')
        content = f.read_bytes()
        f.unlink()
        for _ in range(3):
            ingestor.ingest(str(docs), db_path, vault_id='vault-a')

        with _connect(db_path) as conn:
            task = conn.execute("SELECT file_hash, status FROM tasks").fetchone()
        assert task['status'] == 'MISSING'
        file_hash = task['file_hash']

        f.write_bytes(content)  # file reappears
        ingestor.ingest(str(docs), db_path, vault_id='vault-a')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT status, pre_missing_status FROM tasks WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    assert task['status'] == 'PENDING'
    assert task['pre_missing_status'] is None


def test_multi_vault_file_not_flagged_while_one_copy_survives(tmp_path):
    from unittest.mock import patch
    from core.manager import _connect
    import hashlib

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    content = b'shared content'
    expected_hash = hashlib.sha256(content).hexdigest()

    dir_a = tmp_path / "vault_a"
    dir_a.mkdir()
    (dir_a / "photo.jpg").write_bytes(content)
    dir_b = tmp_path / "vault_b"
    dir_b.mkdir()
    (dir_b / "photo.jpg").write_bytes(content)
    _make_vault(db_path, 'vault-a', str(dir_a))
    _make_vault(db_path, 'vault-b', str(dir_b))

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
        ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

        (dir_a / "photo.jpg").unlink()  # gone from vault-a only
        for _ in range(3):
            ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
            ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT status FROM tasks WHERE file_hash = ?", (expected_hash,)
        ).fetchone()
    assert task['status'] != 'MISSING'  # vault-b copy still exists
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_ingestor.py -v`

Expected: the four new tests FAIL (no `miss_count` tracking exists yet, so no task ever reaches `MISSING`); the four pre-existing tests in this file still PASS (unaffected).

- [ ] **Step 3: Add the five new `core/manager.py` functions**

Add near the other `file_vault`-related functions (after `get_vault_paths`, before `list_tasks`, or any convenient spot in the file — no ordering dependency):

```python
def get_file_vault_rows(db_path, vault_id):
    """Return [{file_hash, file_path, miss_count}, ...] for every file_vault row in this vault."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT file_hash, file_path, miss_count FROM file_vault WHERE vault_id = ?",
            (vault_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_file_vault_miss_count(db_path, file_hash, vault_id, count):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE file_vault SET miss_count = ? WHERE file_hash = ? AND vault_id = ?",
            (count, file_hash, vault_id)
        )
        conn.commit()


def all_file_vault_rows_missing(db_path, file_hash, threshold):
    """True if every file_vault row for this hash has miss_count >= threshold
    (and at least one row exists for this hash)."""
    with _connect(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM file_vault WHERE file_hash = ?", (file_hash,)
        ).fetchone()[0]
        if total == 0:
            return False
        below_threshold = conn.execute(
            "SELECT COUNT(*) FROM file_vault WHERE file_hash = ? AND miss_count < ?",
            (file_hash, threshold)
        ).fetchone()[0]
        return below_threshold == 0


_MISSING_RESTORE_MAP = {
    'PROCESSING': 'PENDING',
    'EMBEDDING': 'EXTRACTED',
}


def flag_task_missing(db_path, file_hash):
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET pre_missing_status = status, status = 'MISSING' "
            "WHERE file_hash = ? AND status != 'MISSING'",
            (file_hash,)
        )
        conn.commit()


def restore_task_from_missing(db_path, file_hash):
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT pre_missing_status FROM tasks WHERE file_hash = ? AND status = 'MISSING'",
            (file_hash,)
        ).fetchone()
        if row is None:
            return
        pre_status = row['pre_missing_status'] or 'PENDING'
        restore_to = _MISSING_RESTORE_MAP.get(pre_status, pre_status)
        conn.execute(
            "UPDATE tasks SET status = ?, pre_missing_status = NULL WHERE file_hash = ?",
            (restore_to, file_hash)
        )
        conn.commit()
```

- [ ] **Step 4: Track seen paths and add the post-walk reconciliation pass in `core/ingestor.py`**

In `ingest()`, add `seen_paths = set()` right after the existing `added = 0` / `moved = 0` initialization (currently lines 81-82):

```python
    added = 0
    moved = 0
    seen_paths = set()
```

In the per-file loop, move the `file_path` computation to the very first line of the loop body (before the `_BLOCKLIST` check) and record it in `seen_paths` immediately — so files skipped by the blocklist/ignore-pattern filters still count as "seen" (an ignore-pattern change must never look like a deletion). Change:

```python
        for name in files:
            if name.lower() in _BLOCKLIST:
                continue
            file_path = os.path.normpath(os.path.join(root, name))
            if _is_hidden_or_system(file_path):
```

to:

```python
        for name in files:
            file_path = os.path.normpath(os.path.join(root, name))
            seen_paths.add(file_path)
            if name.lower() in _BLOCKLIST:
                continue
            if _is_hidden_or_system(file_path):
```

(Everything else in the loop body is unchanged — this only reorders two existing lines and adds one new line.)

After the entire `for root, dirs, files in os.walk(directory):` loop ends, immediately before the final `logger.info(f"Ingestion complete...")` line, add:

```python
    _update_missing_flags(db_path, vault_id, seen_paths)
```

Add the new helper function above `def ingest(...)` (e.g. right after `_update_vector_path`):

```python
def _update_missing_flags(db_path, vault_id, seen_paths):
    """After a vault walk completes, reconcile file_vault.miss_count against what
    was actually seen on disk this cycle. Flips a task to MISSING once every
    file_vault row for its hash has crossed the configured threshold; restores
    it if any row resets to 0 (the file reappeared)."""
    if not vault_id:
        return
    threshold = int(settings.get('ingestion:missing_after_scans') or 3)
    rows = manager.get_file_vault_rows(db_path, vault_id)

    reappeared_hashes = set()
    newly_crossed_hashes = set()

    for row in rows:
        path = os.path.normpath(row['file_path'])
        if path in seen_paths:
            if row['miss_count'] > 0:
                manager.set_file_vault_miss_count(db_path, row['file_hash'], vault_id, 0)
                reappeared_hashes.add(row['file_hash'])
        else:
            new_count = row['miss_count'] + 1
            manager.set_file_vault_miss_count(db_path, row['file_hash'], vault_id, new_count)
            if new_count >= threshold:
                newly_crossed_hashes.add(row['file_hash'])

    for file_hash in newly_crossed_hashes:
        if manager.all_file_vault_rows_missing(db_path, file_hash, threshold):
            manager.flag_task_missing(db_path, file_hash)

    for file_hash in reappeared_hashes:
        manager.restore_task_from_missing(db_path, file_hash)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_ingestor.py tests/test_multi_vault_membership.py -v`

Expected: all tests PASS — the four new ones, the four pre-existing `test_ingestor.py` tests, and every `test_multi_vault_membership.py` test (unaffected by the loop reordering, since `file_path`'s value and the blocklist behavior are unchanged, only computed one line earlier).

- [ ] **Step 6: Commit**

```bash
git add core/ingestor.py core/manager.py tests/test_ingestor.py
git commit -m "$(cat <<'EOF'
feat: detect and soft-flag deleted files during vault scans

ingest() now tracks every path seen during a vault's walk and, after
the walk completes, reconciles file_vault.miss_count for that vault.
A task flips to MISSING only once every file_vault row for its hash
(across all vaults) has crossed ingestion:missing_after_scans -- so
content registered in multiple vaults stays visible while any copy
still exists. Reappearing files restore automatically, mapping a
stale PROCESSING/EMBEDDING pre-missing status to PENDING/EXTRACTED
rather than resuming in-flight work no worker actually owns.
EOF
)"
```

---

### Task 3: Shared purge helper, `_gut_vault` refactor, and missing-files purge endpoint

**Files:**
- Modify: `core/manager.py` (new `purge_file_hashes`, `purge_missing_files`, `purge_missing_files_by_filter`)
- Modify: `core/vault_manager.py:123-157` (`_gut_vault` delegates to `purge_file_hashes`)
- Modify: `api/routes/catalog.py` (new `POST /catalog/missing/purge` endpoint)
- Test: `tests/test_manager.py` (new tests for the three manager functions), existing vault-manager tests re-run as a regression check for the `_gut_vault` refactor

**Interfaces:**
- Consumes: `tasks.status = 'MISSING'` (Task 2).
- Produces: `manager.purge_file_hashes(db_path: str, file_hashes: list[str]) -> None`, `manager.purge_missing_files(db_path: str, file_hashes: list[str]) -> list[str]`, `manager.purge_missing_files_by_filter(db_path: str, vault_id: str = None, q: str = None) -> list[str]` — all three return/consume plain `file_hash` strings, no other task depends on their internals beyond this signature.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_manager.py` (adjust the import block at the top if `_connect` isn't already imported — check the existing top of the file first):

```python
def test_purge_file_hashes_removes_all_rows(tmp_path):
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type) VALUES ('h1', '/a.txt', 'txt')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type) VALUES ('h2', '/b.txt', 'txt')")
        conn.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES ('h1', 0, '/a.txt', 'hello')")
        conn.execute("INSERT INTO extracted_images (source_hash, file_path) VALUES ('h1', '/img.png')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'v1', '/a.txt')")
        conn.commit()

    manager.purge_file_hashes(db_path, ['h1'])

    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h2'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fts_index WHERE file_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extracted_images WHERE source_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM file_vault WHERE file_hash='h1'").fetchone()[0] == 0


def test_purge_missing_files_only_purges_missing_status(tmp_path):
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a.txt', 'txt', 'MISSING')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/b.txt', 'txt', 'COMPLETED')")
        conn.commit()

    purged = manager.purge_missing_files(db_path, ['h1', 'h2'])

    assert purged == ['h1']
    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h2'").fetchone()[0] == 1  # untouched


def test_purge_missing_files_by_filter(tmp_path):
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'A', '/a', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a/report.txt', 'txt', 'MISSING')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/a/photo.jpg', 'jpg', 'MISSING')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', '/a/report.txt')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h2', 'vault-a', '/a/photo.jpg')")
        conn.commit()

    purged = manager.purge_missing_files_by_filter(db_path, q='report')

    assert purged == ['h1']
    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h2'").fetchone()[0] == 1  # untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_manager.py::test_purge_file_hashes_removes_all_rows tests/test_manager.py::test_purge_missing_files_only_purges_missing_status tests/test_manager.py::test_purge_missing_files_by_filter -v`

Expected: all three FAIL with `AttributeError: module 'core.manager' has no attribute 'purge_file_hashes'` (and similarly for the other two functions).

- [ ] **Step 3: Add the three functions to `core/manager.py`**

```python
def purge_file_hashes(db_path: str, file_hashes: list[str]) -> None:
    """Batched hard-delete of tasks + fts_index + extracted_images + file_vault
    rows for the given hashes. extracted_texts cascades via its existing FK.
    Does not touch the vector store -- callers do that themselves (best-effort,
    should never block the SQL delete)."""
    if not file_hashes:
        return
    BATCH_SIZE = 500
    for i in range(0, len(file_hashes), BATCH_SIZE):
        batch = file_hashes[i:i + BATCH_SIZE]
        placeholders = ','.join('?' * len(batch))
        with _connect(db_path) as conn:
            conn.execute(f"DELETE FROM fts_index WHERE file_hash IN ({placeholders})", batch)
            conn.execute(f"DELETE FROM extracted_images WHERE source_hash IN ({placeholders})", batch)
            conn.commit()
    with _connect(db_path) as conn:
        placeholders = ','.join('?' * len(file_hashes))
        conn.execute(f"DELETE FROM tasks WHERE file_hash IN ({placeholders})", file_hashes)
        conn.execute(f"DELETE FROM file_vault WHERE file_hash IN ({placeholders})", file_hashes)
        conn.commit()


def purge_missing_files(db_path: str, file_hashes: list[str]) -> list[str]:
    """Hard-delete the given hashes, but only those currently flagged MISSING
    (safety guard against purging live data via a stale/forged id list).
    Returns the subset of hashes actually purged."""
    if not file_hashes:
        return []
    with _connect(db_path) as conn:
        placeholders = ','.join('?' * len(file_hashes))
        verified = [r[0] for r in conn.execute(
            f"SELECT file_hash FROM tasks WHERE status = 'MISSING' AND file_hash IN ({placeholders})",
            file_hashes
        ).fetchall()]
    purge_file_hashes(db_path, verified)
    return verified


def purge_missing_files_by_filter(db_path: str, vault_id: str = None, q: str = None) -> list[str]:
    """Hard-delete every currently-MISSING task matching the given filter.
    Returns the list of hashes purged."""
    with _connect(db_path) as conn:
        where = ["status = 'MISSING'"]
        params = []
        if vault_id:
            where.append("file_hash IN (SELECT file_hash FROM file_vault WHERE vault_id = ?)")
            params.append(vault_id)
        if q and q.strip():
            where.append("file_path LIKE ?")
            params.append(f"%{q.strip()}%")
        clause = " AND ".join(where)
        verified = [r[0] for r in conn.execute(
            f"SELECT file_hash FROM tasks WHERE {clause}", params
        ).fetchall()]
    purge_file_hashes(db_path, verified)
    return verified
```

- [ ] **Step 4: Refactor `VaultManager._gut_vault` to delegate to `purge_file_hashes`**

Replace the full body of `_gut_vault` in `core/vault_manager.py` (currently lines 123-157):

```python
    def _gut_vault(self, vault_id: str):
        """
        Wipe all extracted content for this vault using batched deletes.
        Each batch is its own short transaction to minimise write-lock duration.
        """
        BATCH_SIZE = 500

        # 1. Read hashes in a short read transaction, then close the connection.
        with _connect(self.db_path) as conn:
            hashes = [r[0] for r in conn.execute(
                "SELECT file_hash FROM tasks WHERE vault_id = ?", (vault_id,)
            ).fetchall()]

        # 2. Delete dependent rows in small batches, each as its own transaction.
        for i in range(0, len(hashes), BATCH_SIZE):
            batch = hashes[i:i + BATCH_SIZE]
            placeholders = ','.join('?' * len(batch))
            with _connect(self.db_path) as conn:
                conn.execute(f"DELETE FROM fts_index WHERE file_hash IN ({placeholders})", batch)
                conn.execute(f"DELETE FROM extracted_images WHERE source_hash IN ({placeholders})", batch)
                conn.commit()

        # 3. Clean up task and vault-membership rows in one final transaction.
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM tasks WHERE vault_id = ?", (vault_id,))
            conn.execute("DELETE FROM file_vault WHERE vault_id = ?", (vault_id,))
            conn.commit()

        # 4. Remove vectors (best-effort — never blocks the delete).
        if hashes:
            try:
                from embeddings.vector_store import VectorStore
                VectorStore().delete_by_hashes(hashes)
            except Exception as e:
                print(f"[vault_manager] Warning: could not remove vectors: {e}")
```

with:

```python
    def _gut_vault(self, vault_id: str):
        """
        Wipe all extracted content for this vault. Batch-delete of tasks/FTS/
        images/file_vault rows is shared with the missing-files purge feature
        via manager.purge_file_hashes; vector cleanup stays a local, best-effort
        step since it's the only part specific to this caller.
        """
        with _connect(self.db_path) as conn:
            hashes = [r[0] for r in conn.execute(
                "SELECT file_hash FROM tasks WHERE vault_id = ?", (vault_id,)
            ).fetchall()]

        from core.manager import purge_file_hashes
        purge_file_hashes(self.db_path, hashes)

        # Remove vectors (best-effort — never blocks the delete).
        if hashes:
            try:
                from embeddings.vector_store import VectorStore
                VectorStore().delete_by_hashes(hashes)
            except Exception as e:
                print(f"[vault_manager] Warning: could not remove vectors: {e}")
```

- [ ] **Step 5: Add the purge endpoint to `api/routes/catalog.py`**

Add `from pydantic import BaseModel` to the imports at the top of `api/routes/catalog.py` (currently lines 1-5), then add this endpoint (anywhere in the file, e.g. after the existing `/catalog` GET route):

```python
class PurgeMissingRequest(BaseModel):
    hashes: list[str] = []
    all: bool = False
    vault_id: str = None
    q: str = None


@router.post("/catalog/missing/purge")
def purge_missing(body: PurgeMissingRequest):
    """Hard-delete confirmed-MISSING tasks. 'all' means every MISSING row
    matching the current vault_id/q filter, not the whole table."""
    db = get_db()
    if body.all:
        purged = manager.purge_missing_files_by_filter(db, vault_id=body.vault_id, q=body.q)
    else:
        purged = manager.purge_missing_files(db, body.hashes)
    if purged:
        try:
            from embeddings.vector_store import VectorStore
            VectorStore().delete_by_hashes(purged)
        except Exception:
            pass
    return {'ok': True, 'purged': len(purged)}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_manager.py -v`

Then re-run the existing vault-manager suite as a regression check on the `_gut_vault` refactor:

Run: `venv/Scripts/python.exe -m pytest tests/unit/test_vault_manager.py -v`

Expected: the three new `test_manager.py` tests PASS, and every pre-existing test in both files still PASSES — `_gut_vault`'s external behavior (what gets deleted, vector cleanup best-effort) is unchanged, only its internals now delegate to `purge_file_hashes`.

- [ ] **Step 7: Commit**

```bash
git add core/manager.py core/vault_manager.py api/routes/catalog.py tests/test_manager.py
git commit -m "$(cat <<'EOF'
feat: add missing-files purge (shared helper, endpoint, _gut_vault refactor)

Extracts _gut_vault's batch-delete logic (fts_index/extracted_images/
tasks/file_vault) into manager.purge_file_hashes so it isn't
duplicated by this feature's purge path. purge_missing_files and
purge_missing_files_by_filter always verify status='MISSING' before
deleting -- a caller-supplied hash list is never trusted blindly.
New POST /catalog/missing/purge wires this to the UI (added next task).
EOF
)"
```

---

### Task 4: Exclude MISSING files from search results

**Files:**
- Modify: `core/manager.py` (`fts_search`, `_filename_filter_clauses`; new `get_missing_hashes`)
- Modify: `search/fts.py` (`_regex_search`, both branches)
- Modify: `search/semantic.py` (`search`, `async_search`)
- Modify: `search/hybrid.py:70` (pass `db_path` through to `semantic.async_search`)
- Modify: `api/routes/search.py:87` (pass `db_path` through to `semantic.async_search`)
- Test: `tests/test_search.py`, `tests/test_multi_vault_membership.py`

**Interfaces:**
- Consumes: `tasks.status = 'MISSING'` (Task 2).
- Produces: `manager.get_missing_hashes(db_path: str) -> set[str]` — consumed by `search/semantic.py`'s two functions, no other task depends on it. `semantic.search(query, top_k=5, hash_filter=None, score_threshold=None, db_path=None)` and `semantic.async_search(query, top_k=5, hash_filter=None, db_path=None)` both gain an optional `db_path` parameter — existing callers that don't pass it keep working (the missing-hash lookup then targets the default DB path via `manager.get_db_path(None)`), but `hybrid.py` and `api/routes/search.py` are updated to pass it explicitly since both already have it in scope.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search.py`:

```python
def test_fts_search_excludes_missing_status(tmp_path):
    from core import manager
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a.txt', 'txt', 'COMPLETED')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/b.txt', 'txt', 'MISSING')")
        conn.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES ('h1', 0, '/a.txt', 'hello world')")
        conn.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES ('h2', 0, '/b.txt', 'hello world')")
        conn.commit()

    results = manager.fts_search(db_path, 'hello')

    assert len(results) == 1
    assert results[0]['file_hash'] == 'h1'


def test_filename_search_excludes_missing_status(tmp_path):
    from core import manager
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/report.txt', 'txt', 'COMPLETED')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/report2.txt', 'txt', 'MISSING')")
        conn.commit()

    results = manager.filename_search(db_path, 'report')

    assert len(results) == 1
    assert results[0]['file_hash'] == 'h1'


def test_get_missing_hashes(tmp_path):
    from core import manager
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a.txt', 'txt', 'COMPLETED')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/b.txt', 'txt', 'MISSING')")
        conn.commit()

    assert manager.get_missing_hashes(db_path) == {'h2'}


def test_semantic_search_excludes_missing_status(tmp_path, monkeypatch):
    from core import manager
    from core.manager import _connect
    from unittest.mock import patch, MagicMock
    import asyncio

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a.txt', 'txt', 'COMPLETED')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/b.txt', 'txt', 'MISSING')")
        conn.commit()

    from search import semantic

    async def _fake_embed(query):
        return [0.1, 0.2, 0.3]

    fake_results = [
        {'file_hash': 'h1', 'file_path': '/a.txt', 'score': 0.9},
        {'file_hash': 'h2', 'file_path': '/b.txt', 'score': 0.8},
    ]

    with patch('embeddings.embedder.async_embed', side_effect=_fake_embed), \
         patch('search.semantic._vs') as mock_vs:
        mock_vs.return_value.search.return_value = fake_results
        results = asyncio.run(semantic.async_search('query', db_path=db_path))

    assert len(results) == 1
    assert results[0]['file_hash'] == 'h1'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_search.py::test_fts_search_excludes_missing_status tests/test_search.py::test_filename_search_excludes_missing_status tests/test_search.py::test_get_missing_hashes tests/test_search.py::test_semantic_search_excludes_missing_status -v`

Expected: `test_fts_search_excludes_missing_status` and `test_filename_search_excludes_missing_status` FAIL (both hashes returned — no status filter yet); `test_get_missing_hashes` FAILS with `AttributeError` (function doesn't exist); `test_semantic_search_excludes_missing_status` FAILS (`async_search` doesn't accept `db_path` yet, or returns both hashes unfiltered).

- [ ] **Step 3: Add `get_missing_hashes` to `core/manager.py`**

```python
def get_missing_hashes(db_path: str) -> set[str]:
    """Return the set of file_hashes currently flagged MISSING. Expected to be
    small relative to total corpus size -- callers use this to post-filter
    already-fetched search candidates, never to build a SQL/LanceDB IN clause."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT file_hash FROM tasks WHERE status = 'MISSING'").fetchall()
        return {r[0] for r in rows}
```

- [ ] **Step 4: Exclude MISSING from FTS (`core/manager.py::fts_search`)**

In `fts_search`, add `t.status != 'MISSING'` to the vault-filtered branch's WHERE list and `tasks.status != 'MISSING'` to the non-vault-filtered branch's WHERE list. The vault-filtered branch currently starts:

```python
            where = ["fts_index.content MATCH ?"]
            params = [*vault_ids, query]
```

Change to:

```python
            where = ["fts_index.content MATCH ?", "t.status != 'MISSING'"]
            params = [*vault_ids, query]
```

The non-vault-filtered branch currently starts:

```python
            where = ["fts_index.content MATCH ?"]
            params = [query]
```

Change to:

```python
            where = ["fts_index.content MATCH ?", "tasks.status != 'MISSING'"]
            params = [query]
```

- [ ] **Step 5: Exclude MISSING from regex search (`search/fts.py::_regex_search`)**

Both branches in `_regex_search` currently start:

```python
        where = ["fts_index.content REGEXP ?"]
        params = [pattern]
```

Change to:

```python
        where = ["fts_index.content REGEXP ?", "tasks.status != 'MISSING'"]
        params = [pattern]
```

(This is the single `where`/`params` initialization shared by both the `vault_ids` and non-`vault_ids` branches in this function — only one change site, not two, since both branches build on the same `where`/`params` lists before branching on `vault_ids`.)

- [ ] **Step 6: Exclude MISSING from filename search (`core/manager.py::_filename_filter_clauses`)**

Add one line to the top of `_filename_filter_clauses` so all three `filename_search` modes (regex/wildcard/plain) get it for free:

```python
def _filename_filter_clauses(file_type, date_from, date_to, vault_ids=None):
    """Return (where_fragments, params) for the common filename filter fields."""
    where, params = ["status != 'MISSING'"], []
    if file_type:
```

(Only the first line changes — `where, params = [], []` becomes `where, params = ["status != 'MISSING'"], []` — everything below it is unchanged.)

- [ ] **Step 7: Post-filter semantic search results (`search/semantic.py`)**

Replace the full contents of `search/semantic.py`:

```python
import asyncio
from embeddings import embedder
from embeddings.vector_store import VectorStore


def _vs() -> VectorStore:
    return VectorStore()


def _threshold() -> float:
    from core.settings import settings
    return float(settings.get('embeddings:score_threshold') or 0.65)


def _missing_hashes(db_path: str) -> set[str]:
    try:
        from core import manager
        return manager.get_missing_hashes(manager.get_db_path(db_path))
    except Exception:
        return set()


def search(query: str, top_k: int = 5, hash_filter: list[str] = None,
           score_threshold: float = None, db_path: str = None) -> list[dict]:
    """Semantic search. Supports optional hash_filter for constraint pre-filtering."""
    vector = embedder.embed(query)
    if not vector:
        return []
    threshold = score_threshold if score_threshold is not None else _threshold()
    try:
        results = _vs().search(vector, top_k=top_k, score_threshold=threshold,
                               hash_filter=hash_filter)
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return []
    missing = _missing_hashes(db_path)
    return [r for r in results if r.get('file_hash') not in missing]


async def async_search(query: str, top_k: int = 5,
                       hash_filter: list[str] = None, db_path: str = None) -> list[dict]:
    """Async semantic search for FastAPI routes."""
    vector = await embedder.async_embed(query)
    if not vector:
        return []
    if hash_filter is not None and len(hash_filter) == 0:
        return []
    try:
        results = await asyncio.to_thread(
            _vs().search, vector,
            top_k, _threshold(), hash_filter,
        )
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return None  # None = failure; [] = success with no results
    missing = await asyncio.to_thread(_missing_hashes, db_path)
    return [r for r in results if r.get('file_hash') not in missing]
```

- [ ] **Step 8: Pass `db_path` through the two live callers**

In `search/hybrid.py`, change:

```python
    semantic_task = asyncio.wait_for(
        semantic.async_search(query, top_k=top_k * 2, hash_filter=hash_filter),
        timeout=sem_timeout,
    )
```

to:

```python
    semantic_task = asyncio.wait_for(
        semantic.async_search(query, top_k=top_k * 2, hash_filter=hash_filter, db_path=db_path),
        timeout=sem_timeout,
    )
```

In `api/routes/search.py`, change:

```python
    if mode == 'semantic':
        results = await semantic.async_search(q, top_k=n, hash_filter=hash_filter)
```

to:

```python
    if mode == 'semantic':
        results = await semantic.async_search(q, top_k=n, hash_filter=hash_filter, db_path=db)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_search.py tests/test_multi_vault_membership.py -v`

Expected: all four new tests PASS, and every pre-existing test in both files still PASSES (the FTS/filename WHERE-clause additions and the semantic post-filter are purely additive — no existing query shape changes for non-MISSING data).

- [ ] **Step 10: Commit**

```bash
git add core/manager.py search/fts.py search/semantic.py search/hybrid.py api/routes/search.py tests/test_search.py
git commit -m "$(cat <<'EOF'
feat: exclude MISSING files from FTS, filename, and semantic search

FTS/filename exclusion is a plain SQL status != 'MISSING' clause
(cheap, no perf concern). Semantic search deliberately does NOT build
a LanceDB hash filter for this -- that's the exact shape of the giant
IN-clause bug fixed earlier (9522b5e). Instead, semantic.py fetches
the small MISSING hash set separately and post-filters already-
returned ANN candidates in Python.
EOF
)"
```

---

### Task 5: Missing Files UI panel in `frontend/vault.html`

**Files:**
- Modify: `frontend/vault.html` (new HTML section between lines 161-163, new JS functions, one addition to the `lc:ready` handler)
- Modify: `frontend/static/i18n/en.json` (three new keys, matching the existing partial-i18n-coverage precedent set by the Art Enrichment Issues panel — button/table text stays hardcoded English, only the accordion badge's title/description/divider label get `data-i18n`)

**Interfaces:**
- Consumes: `GET /api/catalog?status=MISSING&vault_id=...&filename=...` (already exists, no backend change needed — `list_tasks` already supports every filter this panel needs), `POST /api/catalog/missing/purge` (Task 3).
- Produces: nothing consumed by another task — this is the final, UI-facing task.

- [ ] **Step 1: Insert the new HTML section**

In `frontend/vault.html`, between the `</div>` that closes `#vault-log-section` and the `</div><!-- /lc-page -->` that closes the page wrapper (currently lines 161-163):

```html
    </div>

  </div><!-- /lc-page -->
```

Change to:

```html
    </div>

    <!-- == DIVIDER ======================================== -->
    <div class="lc-divider"><div class="lc-divider-label" data-i18n="vault.missing.divider">Missing Files</div></div>

    <div class="lc-bar-badge" onclick="lcAccordion(this)">
      <div class="lc-bar-tab" style="background:#440022;">&#9888;</div>
      <div class="lc-bar-body">
        <div class="lc-bar-name" data-i18n="vault.missing.title">Missing Files</div>
        <div class="lc-bar-desc" data-i18n="vault.missing.desc">Files no longer found on disk — review and purge</div>
        <div class="lc-bar-arrow">&#9654;</div>
      </div>
    </div>
    <div class="lc-bar-expand" id="missing-files-expand">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px;">
        <select id="missing-vault-filter" class="lc-select" onchange="loadMissingFiles(0)">
          <option value="">All vaults</option>
        </select>
        <input id="missing-q-filter" class="lc-input" type="text" placeholder="Search filename..."
               style="flex:1;min-width:160px;" onkeyup="if(event.key==='Enter')loadMissingFiles(0)">
        <button class="lc-btn lc-btn-sm" onclick="loadMissingFiles(0)">Search</button>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:8px;">
        <button class="lc-btn lc-btn-sm" onclick="purgeSelectedMissingFiles()">Purge Selected</button>
        <button class="lc-btn lc-btn-sm lc-btn-danger" onclick="purgeAllMissingFiles()">Purge All (filtered)</button>
      </div>
      <div style="max-height:400px;overflow-y:auto;border:1px solid var(--c-border);border-radius:6px;">
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
          <thead>
            <tr style="background:var(--c-surface);position:sticky;top:0;">
              <th style="padding:6px;text-align:left;"><input type="checkbox" id="missing-select-all" onchange="toggleAllMissingFiles(this)"></th>
              <th style="padding:6px;text-align:left;">File</th>
              <th style="padding:6px;text-align:left;">Vault</th>
              <th style="padding:6px;text-align:left;">Was</th>
              <th style="padding:6px;text-align:left;">Last Updated</th>
            </tr>
          </thead>
          <tbody id="missing-files-body">
            <tr><td colspan="5" style="padding:10px;color:var(--c-label-dim);">Not loaded — expand this panel to load.</td></tr>
          </tbody>
        </table>
      </div>
      <div id="missing-files-pager" style="margin-top:8px;font-size:11px;color:var(--c-label-dim);"></div>
    </div>

  </div><!-- /lc-page -->
```

- [ ] **Step 2: Add the JS functions**

Add this block anywhere in vault.html's `<script>` section (e.g. just before the closing `document.addEventListener('lc:ready', ...)` block, which currently starts at line 1035):

```js
    // ── Missing Files ─────────────────────────────────────────────────────
    let _missingOffset = 0;
    const _missingLimit = 50;
    let _missingVaultsLoaded = false;

    async function loadMissingVaultFilter() {
      if (_missingVaultsLoaded) return;
      try {
        const vaults = await api('/vaults');
        const sel = document.getElementById('missing-vault-filter');
        for (const v of (vaults || [])) {
          const opt = document.createElement('option');
          opt.value = v.vault_id;
          opt.textContent = v.name;
          sel.appendChild(opt);
        }
        _missingVaultsLoaded = true;
      } catch (_) {}
    }

    function _missingQueryString(extra) {
      const vault_id = document.getElementById('missing-vault-filter').value;
      const q = document.getElementById('missing-q-filter').value.trim();
      const params = new URLSearchParams({ status: 'MISSING' });
      if (vault_id) params.set('vault_id', vault_id);
      if (q) params.set('filename', `%${q}%`);
      Object.entries(extra || {}).forEach(([k, v]) => params.set(k, v));
      return params.toString();
    }

    async function loadMissingFiles(offset) {
      _missingOffset = offset || 0;
      const body = document.getElementById('missing-files-body');
      body.innerHTML = `<tr><td colspan="5" style="padding:10px;color:var(--c-label-dim);">Loading...</td></tr>`;
      try {
        const qs = _missingQueryString({ limit: _missingLimit, offset: _missingOffset });
        const data = await api(`/catalog?${qs}`);
        const tasks = data.tasks || [];
        if (!tasks.length) {
          body.innerHTML = `<tr><td colspan="5" style="padding:10px;color:var(--c-ok);">No missing files.</td></tr>`;
        } else {
          body.innerHTML = tasks.map(row => `
            <tr style="border-top:1px solid var(--c-border);">
              <td style="padding:6px;"><input type="checkbox" class="missing-file-check" value="${row.file_hash}"></td>
              <td style="padding:6px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeAttr(row.file_path)}">${formatPath(row.file_path)}</td>
              <td style="padding:6px;">${row.vault_id || '—'}</td>
              <td style="padding:6px;color:var(--c-label-dim);">${row.pre_missing_status || '—'}</td>
              <td style="padding:6px;color:var(--c-label-dim);">${row.last_update || ''}</td>
            </tr>`).join('');
        }
        const total = data.total_matches || 0;
        const shown = tasks.length;
        document.getElementById('missing-files-pager').innerHTML =
          `${_missingOffset + 1}–${_missingOffset + shown} of ${total}` +
          (_missingOffset > 0 ? ` &nbsp; <a href="#" onclick="loadMissingFiles(${Math.max(0, _missingOffset - _missingLimit)});return false;">&laquo; prev</a>` : '') +
          (_missingOffset + shown < total ? ` &nbsp; <a href="#" onclick="loadMissingFiles(${_missingOffset + _missingLimit});return false;">next &raquo;</a>` : '');
      } catch (err) {
        body.innerHTML = `<tr><td colspan="5" style="padding:10px;color:var(--c-error);">${err.message}</td></tr>`;
      }
    }

    function toggleAllMissingFiles(cb) {
      document.querySelectorAll('.missing-file-check').forEach(el => { el.checked = cb.checked; });
    }

    function _selectedMissingFileHashes() {
      return Array.from(document.querySelectorAll('.missing-file-check:checked')).map(el => el.value);
    }

    async function purgeSelectedMissingFiles() {
      const hashes = _selectedMissingFileHashes();
      if (!hashes.length) { lcToast('No files selected'); return; }
      if (!confirm(`Permanently purge ${hashes.length} selected file(s)? This deletes all extracted text, images, and embeddings.`)) return;
      try {
        const result = await api('/catalog/missing/purge', { method: 'POST', body: JSON.stringify({ hashes }) });
        lcToast(`Purged ${result.purged} file(s)`);
        loadMissingFiles(0);
      } catch (err) {
        lcToast('Error: ' + err.message);
      }
    }

    async function purgeAllMissingFiles() {
      const vault_id = document.getElementById('missing-vault-filter').value;
      const q = document.getElementById('missing-q-filter').value.trim();
      if (!confirm('Permanently purge ALL missing files matching the current filter? This deletes all extracted text, images, and embeddings.')) return;
      try {
        const body = { all: true };
        if (vault_id) body.vault_id = vault_id;
        if (q) body.q = q;
        const result = await api('/catalog/missing/purge', { method: 'POST', body: JSON.stringify(body) });
        lcToast(`Purged ${result.purged} file(s)`);
        loadMissingFiles(0);
      } catch (err) {
        lcToast('Error: ' + err.message);
      }
    }
```

- [ ] **Step 3: Wire the initial load into the existing `lc:ready` handler**

In vault.html's `lc:ready` listener, currently ending with:

```js
      // Polling intervals (same as originals)
      setInterval(loadStats, 5000);
      setInterval(loadVaultCards, 30000);
      setInterval(loadUnknowns, 30000);
      setInterval(loadCatalog, 15000);
    });
```

Change to:

```js
      // Polling intervals (same as originals)
      setInterval(loadStats, 5000);
      setInterval(loadVaultCards, 30000);
      setInterval(loadUnknowns, 30000);
      setInterval(loadCatalog, 15000);

      loadMissingVaultFilter();
      loadMissingFiles(0);
    });
```

- [ ] **Step 4: Add i18n keys**

In `frontend/static/i18n/en.json`, add these three keys (anywhere in the flat key list — alongside the other `vault.*` keys is most consistent):

```json
  "vault.missing.divider": "Missing Files",
  "vault.missing.title": "Missing Files",
  "vault.missing.desc": "Files no longer found on disk — review and purge",
```

`es.json`/`fr.json` are intentionally left untouched in this task — the Art Enrichment Issues panel this is modeled on has the same partial-coverage gap (only badge title/desc are internationalized, not button/table text), so this matches existing precedent rather than exceeding it.

- [ ] **Step 5: Manual verification**

This task has no automated test — it is markup/JS with no existing frontend test harness in this repo to extend (`utils.html`'s equivalent Art Enrichment Issues panel has none either). Verify manually:

1. Start the server (`python run.py`), open `/vault` in a browser.
2. Confirm the new "Missing Files" divider and accordion badge render below the Vault Log table, and the badge expands/collapses via `lcAccordion` like every other panel on the page.
3. Manually flag a task `MISSING` via SQLite (`UPDATE tasks SET status='MISSING', pre_missing_status='PENDING' WHERE file_hash=...`), reload the page, expand the panel, and confirm the row appears with the right vault/pre-missing-status/last-updated values.
4. Click "Purge Selected" on that row, confirm the browser `confirm()` dialog appears, confirm it, and verify the row disappears and a toast reports 1 file purged.
5. Confirm the vault filter `<select>` populates from `/api/vaults` and that changing it re-queries the list.

- [ ] **Step 6: Commit**

```bash
git add frontend/vault.html frontend/static/i18n/en.json
git commit -m "$(cat <<'EOF'
feat: add Missing Files panel to Vault Log page

New accordion panel (modeled on the Art Enrichment Issues panel in
utils.html) lists MISSING tasks via the existing GET /catalog?status=
MISSING endpoint -- no new list endpoint needed. Purge Selected/Purge
All wire to the POST /catalog/missing/purge endpoint added in the
previous task. Manually verified end-to-end (no existing frontend
test harness to extend for this panel).
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- §4 schema + setting → Task 1. ✓
- §5 detection (seen-path tracking, per-vault-membership miss_count, all-vaults-missing check) → Task 2. ✓
- §6 restore mapping (PROCESSING→PENDING, EMBEDDING→EXTRACTED, else verbatim) → Task 2 (`_MISSING_RESTORE_MAP`, `restore_task_from_missing`). ✓
- §7 purge (shared helper extracted from `_gut_vault`, safety-verified purge functions) → Task 3. ✓
- §8 API → folded into Task 3 once `GET /api/catalog?status=MISSING` was confirmed to already cover the list requirement — only the purge endpoint needed building, so no separate `api/routes/missing_files.py` file was created (avoids an unnecessary new file for a single route; the design spec's §8 table is superseded by this simplification, noted in Task 3's commit message).
- §9 search exclusion (FTS SQL filter, semantic Python post-filter avoiding the LanceDB IN-clause bug, Vault Log left unchanged, worker claims unaffected) → Task 4. ✓
- §10 UI panel → Task 5. ✓
- §11 rollout (additive migration, no behavior change for present files) → Task 1's `ALTER TABLE ADD COLUMN` with defaults; no dedicated task needed, it's a property of Task 1's design, not a separate step.

**Placeholder scan:** No TBD/TODO/"add appropriate" phrasing; every step has complete, copy-pasteable code. Task 5's manual-verification step is not a placeholder — it's an explicit, deliberate substitute for automated coverage where no test harness exists, matching how the codebase already treats this class of change (the Art Enrichment Issues panel it's modeled on has no automated frontend test either).

**Type consistency:**
- `get_file_vault_rows`/`set_file_vault_miss_count`/`all_file_vault_rows_missing`/`flag_task_missing`/`restore_task_from_missing` (Task 2) are internal to `ingestor.py`'s `_update_missing_flags` — no cross-task signature drift risk since nothing else calls them.
- `purge_file_hashes(db_path, file_hashes) -> None` vs `purge_missing_files(...) -> list[str]` vs `purge_missing_files_by_filter(...) -> list[str]` (Task 3) — the two `list[str]`-returning functions both feed the same `purged` variable shape in Task 3 Step 5's endpoint and Task 5's frontend `result.purged` (used as `.length` there — wait, `result.purged` in the frontend is `len(purged)`, an int, from the endpoint's `{'ok': True, 'purged': len(purged)}` response — confirmed consistent between Task 3 Step 5's endpoint code and Task 5's `lcToast(\`Purged ${result.purged} file(s)\`)` usage, which treats it as a number, not a list. No mismatch.
- `semantic.search`/`async_search`'s new `db_path` parameter (Task 4) is optional (`= None`) in both functions, so the one pre-existing test call site (`tests/test_search.py`'s existing `semantic.search('brown fox', top_k=5)`, not shown in this plan but confirmed present in the codebase) keeps working unchanged — `_missing_hashes(None)` resolves via `manager.get_db_path(None)`, which returns the default configured DB path rather than erroring.

Five tasks total, each independently testable and committable; only Task 5 lacks automated coverage, for the reason stated above.
