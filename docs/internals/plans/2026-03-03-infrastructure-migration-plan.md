# Infrastructure Migration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate DocVault's data stores, worker infrastructure, and UI to support multiple vaults, structured observability, and autonomous resource management — before building any new end-user features.

**Architecture:** Four independently-testable layers delivered in sequence, each gated by a CLI test rig. Existing extractors are wrapped in a `LegacyExtractorAdapter` (strangler-fig pattern) so they continue working while the new `BaseExtractor` contract is established. The global `settings` singleton is preserved for backward compatibility while a new `SettingsResolver` adds vault-aware 4-tier lookup.

**Tech Stack:** Python 3.13, SQLite (WAL mode), FastAPI, Qdrant, psutil, pynvml, wmi (new), argparse (test rigs)

**Reference:** `docs/plans/2026-03-03-infrastructure-migration-design.md`

---

## Setup

### Task 0: Install dependencies and create directory structure

**Files:**
- Modify: `requirements.txt`

**Step 1: Install new packages**

```bash
cd E:\DocVault
venv\Scripts\pip install psutil pynvml wmi
```

Expected output: Successfully installed psutil-x.x.x pynvml-x.x.x wmi-x.x.x

**Step 2: Add to requirements.txt**

Open `requirements.txt` and add these three lines (preserve existing entries):
```
psutil
pynvml
wmi
```

**Step 3: Create new directories**

```bash
mkdir tests
mkdir tests\rigs
mkdir tests\unit
mkdir core\extractors
```

Create empty `__init__.py` files:
```bash
type nul > tests\__init__.py
type nul > tests\unit\__init__.py
type nul > core\extractors\__init__.py
```

**Step 4: Commit**

```bash
git add requirements.txt tests\ core\extractors\__init__.py
git commit -m "chore: install governor deps, scaffold tests/ and core/extractors/"
```

---

## Layer 1 — DB / Schema Foundation

**Gate:** `python tests/rigs/check_db_migration.py` exits 0

---

### Task 1.1: Add logs.db path and init functions to `core/manager.py`

**Files:**
- Modify: `core/manager.py`

**Step 1: Write the test (will fail — function doesn't exist yet)**

Create `tests/unit/test_logs_db.py`:

```python
import sqlite3, tempfile, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def test_init_logs_db_creates_all_tables(tmp_path, monkeypatch):
    """init_logs_db() must create all four tables."""
    import core.manager as m
    db = str(tmp_path / 'logs.db')
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: db)
    m.init_logs_db()
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert {'task_timings', 'worker_errors', 'worker_log',
            'extractor_stats', 'system_stats'}.issubset(tables)
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_logs_db.py -v
```
Expected: FAILED — `module 'core.manager' has no attribute 'get_logs_db_path'`

**Step 3: Implement in `core/manager.py`**

Add after the `get_settings_db_path()` function (around line 19):

```python
def get_logs_db_path():
    """Returns the path to logs.db, alongside the main database."""
    main_db = get_db_path()
    return os.path.join(os.path.dirname(main_db), 'logs.db')


def init_logs_db():
    """Create logs.db with observability tables. Idempotent."""
    db_path = get_logs_db_path()
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_timings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash     TEXT,
                vault_id      TEXT,
                extractor     TEXT,
                file_size     INTEGER,
                page_count    INTEGER,
                duration_secs REAL,
                elapsed_secs  REAL,
                completed_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS worker_errors (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash     TEXT,
                vault_id      TEXT,
                extractor     TEXT,
                error_type    TEXT,
                error_message TEXT,
                traceback     TEXT,
                occurred_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS worker_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_hash     TEXT,
                vault_id      TEXT,
                extractor     TEXT,
                level         TEXT,
                message       TEXT,
                occurred_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS extractor_stats (
                vault_id      TEXT,
                extractor     TEXT,
                sample_count  INTEGER DEFAULT 0,
                avg_secs      REAL DEFAULT 0,
                p50_secs      REAL DEFAULT 0,
                p95_secs      REAL DEFAULT 0,
                last_updated  TEXT,
                PRIMARY KEY (vault_id, extractor)
            );

            CREATE TABLE IF NOT EXISTS system_stats (
                sampled_at    TEXT PRIMARY KEY,
                cpu_pct       REAL,
                cpu_temp      REAL,
                ram_used_gb   REAL,
                ram_total_gb  REAL,
                ram_pct       REAL,
                gpu_temp      REAL,
                gpu_util_pct  REAL,
                vram_used_gb  REAL,
                vram_total_gb REAL,
                throttle_state TEXT
            );
        """)
        conn.commit()
```

**Step 4: Run test to confirm pass**

```bash
venv\Scripts\pytest tests/unit/test_logs_db.py -v
```
Expected: PASSED

**Step 5: Commit**

```bash
git add core/manager.py tests/unit/test_logs_db.py
git commit -m "feat(layer1): add logs.db init — task_timings, worker_errors, worker_log, extractor_stats, system_stats"
```

---

### Task 1.2: Add `vaults` table and `vault_id` migration to `init_db()`

**Files:**
- Modify: `core/manager.py`

**Step 1: Write the test**

Create `tests/unit/test_vaults_schema.py`:

```python
import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def test_vaults_table_exists(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    m.init_db(db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert 'vaults' in tables

def test_tasks_has_vault_id(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    m.init_db(db)
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    conn.close()
    assert 'vault_id' in cols
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_vaults_schema.py -v
```
Expected: FAILED — vaults table not found, vault_id column not found

**Step 3: Implement — add to `init_db()` in `core/manager.py`**

Inside the `conn.executescript(...)` call in `init_db()`, add after the `fts_index` virtual table:

```python
            CREATE TABLE IF NOT EXISTS vaults (
                vault_id       TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                scan_directory TEXT NOT NULL,
                priority       INTEGER DEFAULT 5,
                color          TEXT DEFAULT '#6366f1',
                state          TEXT DEFAULT 'active',
                created_at     TEXT,
                updated_at     TEXT
            );
```

Then in the schema migrations section (after existing `ALTER TABLE` checks), add:

```python
        if 'vault_id' not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN vault_id TEXT REFERENCES vaults(vault_id)")
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_vaults_schema.py -v
```
Expected: PASSED

**Step 5: Commit**

```bash
git add core/manager.py tests/unit/test_vaults_schema.py
git commit -m "feat(layer1): add vaults table and vault_id column to tasks"
```

---

### Task 1.3: Add `vault_settings` table to `init_settings_db()`

**Files:**
- Modify: `core/manager.py`

**Step 1: Write the test**

Add to `tests/unit/test_vaults_schema.py`:

```python
def test_vault_settings_table_exists(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    m.init_settings_db()
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert 'vault_settings' in tables
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_vaults_schema.py::test_vault_settings_table_exists -v
```

**Step 3: Implement — add to `init_settings_db()` in `core/manager.py`**

Add to the `executescript` in `init_settings_db()`:

```python
            CREATE TABLE IF NOT EXISTS vault_settings (
                vault_id TEXT NOT NULL,
                key      TEXT NOT NULL,
                value    TEXT NOT NULL,
                PRIMARY KEY (vault_id, key)
            );
```

**Step 4: Run and commit**

```bash
venv\Scripts\pytest tests/unit/test_vaults_schema.py -v
git add core/manager.py tests/unit/test_vaults_schema.py
git commit -m "feat(layer1): add vault_settings table to settings.db"
```

---

### Task 1.4: Default vault bootstrap

**Files:**
- Modify: `core/manager.py`

**Step 1: Write the test**

Create `tests/unit/test_vault_bootstrap.py`:

```python
import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _make_dbs(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db()
    m.init_db(db)
    return m, db, sdb

def test_bootstrap_creates_documents_vault(tmp_path, monkeypatch):
    m, db, sdb = _make_dbs(tmp_path, monkeypatch)
    m.bootstrap_default_vault(db, scan_directory='E:/test/docs')
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT name, scan_directory FROM vaults").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 'Documents'
    assert row[1] == 'E:/test/docs'

def test_bootstrap_assigns_existing_tasks(tmp_path, monkeypatch):
    m, db, sdb = _make_dbs(tmp_path, monkeypatch)
    # Insert a task with no vault_id
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO tasks (file_hash, file_path, file_type) VALUES ('abc123', '/a/b.pdf', 'pdf')"
    )
    conn.commit(); conn.close()
    m.bootstrap_default_vault(db, scan_directory='E:/test/docs')
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT vault_id FROM tasks WHERE file_hash='abc123'").fetchone()
    conn.close()
    assert row[0] is not None

def test_bootstrap_is_idempotent(tmp_path, monkeypatch):
    m, db, sdb = _make_dbs(tmp_path, monkeypatch)
    m.bootstrap_default_vault(db, 'E:/test/docs')
    m.bootstrap_default_vault(db, 'E:/test/docs')  # second call must not raise or duplicate
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
    conn.close()
    assert count == 1
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_vault_bootstrap.py -v
```

**Step 3: Implement `bootstrap_default_vault()` in `core/manager.py`**

Add after `init_db()`:

```python
def bootstrap_default_vault(db_path=None, scan_directory=None):
    """
    If no vaults exist, create the 'Documents' default vault from the current
    scan_directory and assign all un-vaulted tasks to it. Idempotent.
    """
    import uuid
    from datetime import datetime, timezone

    db_path = get_db_path(db_path)
    with _connect(db_path) as conn:
        existing = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
        if existing > 0:
            return  # already bootstrapped

        if not scan_directory:
            # Read from settings.db as fallback
            try:
                scan_directory = get_setting('paths:scan_directory') or '.'
            except Exception:
                scan_directory = '.'

        vault_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
               VALUES (?, 'Documents', ?, 5, 'active', ?, ?)""",
            (vault_id, scan_directory, now, now)
        )
        conn.execute(
            "UPDATE tasks SET vault_id = ? WHERE vault_id IS NULL",
            (vault_id,)
        )
        conn.commit()
        print(f"[bootstrap] Created default vault 'Documents' ({vault_id}) from {scan_directory}")
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_vault_bootstrap.py -v
```
Expected: 3 PASSED

**Step 5: Commit**

```bash
git add core/manager.py tests/unit/test_vault_bootstrap.py
git commit -m "feat(layer1): bootstrap_default_vault — creates Documents vault, assigns existing tasks"
```

---

### Task 1.5: Wire `init_logs_db()` and `bootstrap_default_vault()` into `run.py`

**Files:**
- Modify: `run.py`

**Step 1: Update the `start()` function**

Replace the DB init block in `run.py`:

```python
def start():
    # Init DBs in order — settings.db first (survives resets), then main, then logs
    manager.init_settings_db()
    manager.init_db(DB_PATH)
    manager.init_logs_db()

    # Bootstrap default vault if this is a first run or migration from pre-vault version
    scan_dir = settings.get('paths:scan_directory')
    manager.bootstrap_default_vault(DB_PATH, scan_directory=scan_dir)
    # ... rest of start() unchanged
```

**Step 2: Verify server starts cleanly**

```bash
venv\Scripts\python run.py
```
Expected output includes:
- `[bootstrap] Created default vault 'Documents' ...` (first run only)
- Server starts at http://localhost:8000
- No tracebacks

Stop with Ctrl+C.

**Step 3: Commit**

```bash
git add run.py
git commit -m "feat(layer1): wire init_logs_db and bootstrap_default_vault into startup"
```

---

### Task 1.6: Write `tests/rigs/check_db_migration.py`

**Files:**
- Create: `tests/rigs/check_db_migration.py`

**Step 1: Write the rig**

```python
#!/usr/bin/env python3
"""
check_db_migration.py — Validates Layer 1 DB schema migration for DocVault.

Verifies that after a server startup:
  - docvault.db has a 'vaults' table with correct columns
  - docvault.db tasks table has a 'vault_id' column
  - At least one vault exists (the 'Documents' default)
  - All tasks have a non-null vault_id
  - settings.db has a 'vault_settings' table
  - logs.db exists with all five required tables

Exit codes:
  0 — all checks passed
  1 — one or more checks failed

Usage:
  python tests/rigs/check_db_migration.py
  python tests/rigs/check_db_migration.py --verbose
  python tests/rigs/check_db_migration.py --db path/to/docvault.db
"""

import argparse
import sqlite3
import sys
import os

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
RESET  = '\033[0m'

def _pass(msg, verbose): print(f"{GREEN}PASS{RESET}  {msg}")
def _fail(msg):          print(f"{RED}FAIL{RESET}  {msg}"); return False
def _warn(msg):          print(f"{YELLOW}WARN{RESET}  {msg}")


def check(label, condition, verbose=False, detail=''):
    if condition:
        _pass(label, verbose)
        if verbose and detail:
            print(f"       {detail}")
        return True
    return _fail(f"{label}  {detail}")


def run_checks(db_path, settings_db_path, logs_db_path, verbose):
    results = []

    # --- docvault.db checks ---
    if not os.path.exists(db_path):
        results.append(_fail(f"docvault.db not found at {db_path}"))
        return results

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    results.append(check("vaults table exists", 'vaults' in tables, verbose))

    if 'vaults' in tables:
        vault_cols = {r[1] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()}
        for col in ('vault_id', 'name', 'scan_directory', 'priority', 'color', 'state'):
            results.append(check(f"  vaults.{col} column", col in vault_cols, verbose))

        vault_count = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
        results.append(check(
            f"At least one vault exists ({vault_count} found)",
            vault_count > 0, verbose
        ))
        if vault_count > 0:
            names = [r[0] for r in conn.execute("SELECT name FROM vaults").fetchall()]
            results.append(check(
                "'Documents' vault exists",
                'Documents' in names, verbose,
                detail=f"Found: {names}"
            ))

    if 'tasks' in tables:
        task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        results.append(check("tasks.vault_id column exists", 'vault_id' in task_cols, verbose))

        if 'vault_id' in task_cols:
            unvaulted = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE vault_id IS NULL"
            ).fetchone()[0]
            results.append(check(
                f"All tasks have vault_id (unvaulted: {unvaulted})",
                unvaulted == 0, verbose
            ))
    conn.close()

    # --- settings.db checks ---
    if not os.path.exists(settings_db_path):
        results.append(_fail(f"settings.db not found at {settings_db_path}"))
    else:
        conn2 = sqlite3.connect(settings_db_path)
        stables = {r[0] for r in conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        results.append(check("vault_settings table in settings.db", 'vault_settings' in stables, verbose))
        conn2.close()

    # --- logs.db checks ---
    if not os.path.exists(logs_db_path):
        results.append(_fail(f"logs.db not found at {logs_db_path}"))
    else:
        conn3 = sqlite3.connect(logs_db_path)
        ltables = {r[0] for r in conn3.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for t in ('task_timings', 'worker_errors', 'worker_log', 'extractor_stats', 'system_stats'):
            results.append(check(f"logs.db has '{t}' table", t in ltables, verbose))
        conn3.close()

    return results


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--db',          default='docvault.db',
                        help='Path to docvault.db (default: docvault.db)')
    parser.add_argument('--settings-db', default='settings.db',
                        help='Path to settings.db (default: settings.db)')
    parser.add_argument('--logs-db',     default='logs.db',
                        help='Path to logs.db (default: logs.db)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show detail for each check')
    args = parser.parse_args()

    print(f"\nDocVault Layer 1 — DB Migration Check")
    print(f"  docvault.db : {args.db}")
    print(f"  settings.db : {args.settings_db}")
    print(f"  logs.db     : {args.logs_db}\n")

    results = run_checks(args.db, args.settings_db, args.logs_db, args.verbose)
    passed = sum(1 for r in results if r is True or r)
    failed = sum(1 for r in results if r is False)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
```

**Step 2: Start the server once to create the DB files, then run the rig**

```bash
venv\Scripts\python run.py &
# wait ~5 seconds for startup, then Ctrl+C
python tests/rigs/check_db_migration.py --verbose
```
Expected: all PASS, exit 0

**Step 3: Commit**

```bash
git add tests/rigs/check_db_migration.py
git commit -m "test(layer1): add check_db_migration.py rig — Layer 1 gate"
```

**Layer 1 complete. Gate: `python tests/rigs/check_db_migration.py` exits 0.**

---

## Layer 2 — Extractor Contracts, Settings, Scheduling, Resource Governor

**Gate:** All three Layer 2 rigs exit 0.

---

### Task 2.1: Create extractor contract base classes

**Files:**
- Create: `core/extractors/base.py`

**Step 1: Write unit tests first**

Create `tests/unit/test_extractor_contracts.py`:

```python
import threading, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.extractors.base import (
    BaseExtractor, ExtractorContext, IngestResult,
    LegacyExtractorAdapter, ExtractorLogger
)
import core.manager as m

def _ctx(tmp_path, monkeypatch):
    ldb = str(tmp_path / 'logs.db')
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: ldb)
    m.init_logs_db()
    return ExtractorContext(
        vault_id='v1', file_hash='h1',
        cancel_token=threading.Event(),
        logger=ExtractorLogger('test', 'v1', 'h1'),
        settings=None, timeout_secs=None
    )

def test_ingest_result_defaults():
    r = IngestResult(text='hello', metadata={})
    assert r.status == 'success'
    assert r.child_tasks == []
    assert r.errors == []

def test_cancel_token_stops_legacy_adapter(tmp_path, monkeypatch):
    """LegacyExtractorAdapter.run() checks cancel_token before calling extract."""
    ctx = _ctx(tmp_path, monkeypatch)
    ctx.cancel_token.set()  # pre-cancelled

    calls = []
    class FakeExtractor:
        __name__ = 'fake'
        def extract(self, path):
            calls.append(path)
            return ('text', None)

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'cancelled'
    assert calls == []  # extract was never called

def test_legacy_adapter_text_result(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)

    class FakeExtractor:
        __name__ = 'fake'
        def extract(self, path):
            return ('extracted text', None)

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'success'
    assert result.text == 'extracted text'

def test_legacy_adapter_error_result(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, monkeypatch)

    class FakeExtractor:
        __name__ = 'fake'
        def extract(self, path):
            return (None, 'something went wrong')

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'failed'
    assert len(result.errors) == 1
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_extractor_contracts.py -v
```
Expected: ImportError — `core.extractors.base` does not exist yet

**Step 3: Implement `core/extractors/base.py`**

```python
"""
core/extractors/base.py — DocVault extractor contract system.

All extractors implement BaseExtractor. Workers only ever call .run().
Legacy extractors (returning (result, err) tuples) are wrapped via LegacyExtractorAdapter.

Extractor type hierarchy:
  BaseExtractor
    UtilityExtractor     — deterministic tools (Tesseract, Poppler, ffmpeg)
    IntelligentExtractor — single-prompt AI (Ollama vision, Whisper)
      ChatExtractor      — multi-turn AI with history
    PipelineExtractor    — orchestrates other extractors in sequence
    RemoteExtractor      — external API with auth + retry
    ArchiveExtractor     — container formats yielding child tasks
    SynthesisExtractor   — post-extraction enrichment (summary, tags)
"""

from __future__ import annotations
import threading
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ExtractedImage:
    file_path: str
    page_num: int | None = None
    image_index: int | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class ChildTask:
    """Produced by ArchiveExtractor — a sub-document to queue for extraction."""
    file_path: str
    file_type: str
    vault_id: str
    priority: int = 10


@dataclass
class Enrichment:
    """Produced by SynthesisExtractor — derived metadata."""
    kind: str        # 'summary' | 'tags' | 'entities' | ...
    value: Any


@dataclass
class ExtractError:
    extractor_name: str
    error_type: str
    message: str
    tb: str = ''


@dataclass
class IngestResult:
    """Universal envelope returned by every extractor to the worker."""
    text: str | None
    metadata: dict = field(default_factory=dict)
    images: list[ExtractedImage] = field(default_factory=list)
    child_tasks: list[ChildTask] = field(default_factory=list)
    enrichments: list[Enrichment] = field(default_factory=list)
    errors: list[ExtractError] = field(default_factory=list)
    status: str = 'success'          # success | partial | failed | cancelled
    extractor_name: str = ''
    elapsed_secs: float = 0.0


# ---------------------------------------------------------------------------
# ExtractorLogger — writes structured records to logs.db
# ---------------------------------------------------------------------------

class ExtractorLogger:
    """Thin structured logger that writes to logs.db worker_errors and worker_log tables."""

    LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')

    def __init__(self, extractor_name: str, vault_id: str, file_hash: str,
                 debug_enabled: bool = False):
        self.extractor_name = extractor_name
        self.vault_id = vault_id
        self.file_hash = file_hash
        self.debug_enabled = debug_enabled

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_log(self, level: str, message: str):
        try:
            from core.manager import get_logs_db_path, _connect
            with _connect(get_logs_db_path()) as conn:
                conn.execute(
                    """INSERT INTO worker_log
                       (file_hash, vault_id, extractor, level, message, occurred_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (self.file_hash, self.vault_id, self.extractor_name,
                     level, message, self._now())
                )
                conn.commit()
        except Exception:
            pass  # never let logging kill an extractor

    def _write_error(self, level: str, message: str, error_type: str = '', tb: str = ''):
        try:
            from core.manager import get_logs_db_path, _connect
            with _connect(get_logs_db_path()) as conn:
                conn.execute(
                    """INSERT INTO worker_errors
                       (file_hash, vault_id, extractor, error_type, error_message, traceback, occurred_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (self.file_hash, self.vault_id, self.extractor_name,
                     error_type or level, message, tb, self._now())
                )
                conn.commit()
        except Exception:
            pass

    def debug(self, msg: str):
        if self.debug_enabled:
            self._write_log('DEBUG', msg)

    def info(self, msg: str):
        self._write_log('INFO', msg)

    def warning(self, msg: str):
        self._write_log('WARNING', msg)
        self._write_error('WARNING', msg)

    def error(self, msg: str, exc: Exception | None = None):
        tb = traceback.format_exc() if exc else ''
        self._write_error('ERROR', msg, type(exc).__name__ if exc else '', tb)

    def critical(self, msg: str, exc: Exception | None = None):
        tb = traceback.format_exc() if exc else ''
        self._write_error('CRITICAL', msg, type(exc).__name__ if exc else '', tb)


# ---------------------------------------------------------------------------
# ExtractorContext — per-invocation execution environment
# ---------------------------------------------------------------------------

@dataclass
class ExtractorContext:
    vault_id: str
    file_hash: str
    cancel_token: threading.Event
    logger: ExtractorLogger
    settings: Any                    # SettingsResolver — injected by worker
    timeout_secs: int | None = None


# ---------------------------------------------------------------------------
# BaseExtractor — the contract all extractors satisfy
# ---------------------------------------------------------------------------

class BaseExtractor(ABC):
    """
    Workers call .run(). Never call .extract() directly.

    Subclass and implement:
      extract(file_path, ctx) -> any type-specific result
      normalize(result, ctx)  -> IngestResult
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique extractor identifier used in logs and timing records."""
        ...

    @abstractmethod
    def extract(self, file_path: Path, ctx: ExtractorContext):
        """Perform extraction. Check ctx.cancel_token at safe checkpoints."""
        ...

    @abstractmethod
    def normalize(self, result, ctx: ExtractorContext) -> IngestResult:
        """Convert type-specific result to IngestResult."""
        ...

    def run(self, file_path: Path | str, ctx: ExtractorContext) -> IngestResult:
        """Entry point for workers. Enforces cancel check, times execution."""
        file_path = Path(file_path)
        start = time.monotonic()

        if ctx.cancel_token.is_set():
            ctx.logger.warning(f"Cancelled before start")
            return IngestResult(text=None, status='cancelled',
                                extractor_name=self.name, elapsed_secs=0.0)
        try:
            raw = self.extract(file_path, ctx)
            result = self.normalize(raw, ctx)
        except Exception as exc:
            ctx.logger.error(f"Unhandled exception in {self.name}", exc)
            result = IngestResult(
                text=None, status='failed', extractor_name=self.name,
                errors=[ExtractError(self.name, type(exc).__name__, str(exc),
                                     traceback.format_exc())]
            )

        result.extractor_name = self.name
        result.elapsed_secs = time.monotonic() - start
        return result


# ---------------------------------------------------------------------------
# LegacyExtractorAdapter — wraps old (result, err) extractors
# ---------------------------------------------------------------------------

class LegacyExtractorAdapter(BaseExtractor):
    """
    Wraps a legacy extractor module that uses the old interface:
        extractor.extract(file_path) -> (result, err)

    result may be: str | list[dict] | dict | None
    err may be: str | None
    """

    def __init__(self, legacy_extractor):
        self._ext = legacy_extractor

    @property
    def name(self) -> str:
        return getattr(self._ext, '__name__', str(self._ext))

    def extract(self, file_path: Path, ctx: ExtractorContext):
        return self._ext.extract(str(file_path))

    def normalize(self, result, ctx: ExtractorContext) -> IngestResult:
        # result here is the (value, err) tuple from the legacy extractor
        value, err = result if isinstance(result, tuple) else (result, None)

        errors = []
        if err:
            errors.append(ExtractError(self.name, 'ExtractorError', err))

        # str → main text
        if isinstance(value, str) and value:
            return IngestResult(text=value, errors=errors,
                                status='failed' if (err and not value) else 'success')

        # list[dict] → images (image_extractor pattern)
        if isinstance(value, list):
            images = []
            text_parts = []
            for img_meta in value:
                desc = img_meta.pop('description', None)
                images.append(ExtractedImage(**{
                    k: img_meta.get(k) for k in
                    ('file_path', 'page_num', 'image_index', 'width', 'height')
                    if k in img_meta
                }))
                if desc:
                    label = f"[Image — page {img_meta.get('page_num', '?')}]"
                    text_parts.append(f"{label}\n{desc}")
            return IngestResult(
                text="\n\n".join(text_parts) if text_parts else None,
                images=images, errors=errors,
                status='failed' if (err and not images) else 'success'
            )

        # dict → metadata
        if isinstance(value, dict):
            return IngestResult(text=None, metadata=value, errors=errors,
                                status='failed' if (err and not value) else 'success')

        # None with error
        if err:
            return IngestResult(text=None, errors=errors, status='failed')

        return IngestResult(text=None, status='success')


# ---------------------------------------------------------------------------
# Type-specific base classes (properties only — no implementation)
# ---------------------------------------------------------------------------

class UtilityExtractor(BaseExtractor):
    """Deterministic tool-based extractor (Tesseract, Poppler, ffmpeg, etc.)."""
    tool_path: str = ''
    supported_formats: list[str] = []


class IntelligentExtractor(BaseExtractor):
    """Single-prompt AI extractor (Ollama vision, Whisper, Claude vision, etc.)."""
    model: str = ''
    provider: str = 'ollama'
    temperature: float = 0.1
    system_prompt: str = ''


class ChatExtractor(IntelligentExtractor):
    """Multi-turn AI extractor with conversation history."""
    history: list[dict] = []


class PipelineExtractor(BaseExtractor):
    """Runs a sequence of extractors, stops when stop_condition is met."""
    stages: list[BaseExtractor] = []

    def stop_condition(self, result: IngestResult) -> bool:
        """Return True if this stage's result is sufficient."""
        return bool(result.text and len(result.text) > 50)


class RemoteExtractor(BaseExtractor):
    """External API extractor with auth lifecycle and retry."""
    endpoint: str = ''
    rate_limit: float = 1.0   # requests per second


class ArchiveExtractor(BaseExtractor):
    """Container format extractor — yields child tasks rather than text."""
    max_depth: int = 3


class SynthesisExtractor(BaseExtractor):
    """Post-extraction enrichment — operates on already-extracted text."""
    prompt_template: str = ''
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_extractor_contracts.py -v
```
Expected: 4 PASSED

**Step 5: Commit**

```bash
git add core/extractors/base.py tests/unit/test_extractor_contracts.py
git commit -m "feat(layer2): add extractor contract system — BaseExtractor, ExtractorContext, IngestResult, LegacyExtractorAdapter"
```

---

### Task 2.2: Add vault-aware `SettingsResolver` to `core/settings.py`

**Files:**
- Modify: `core/settings.py`

**Step 1: Write tests**

Create `tests/unit/test_settings_resolver.py`:

```python
import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _make_settings_db(tmp_path, global_overrides=None, vault_overrides=None):
    db = str(tmp_path / 'settings.db')
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("""CREATE TABLE vault_settings
                    (vault_id TEXT, key TEXT, value TEXT, PRIMARY KEY(vault_id,key))""")
    for k, v in (global_overrides or {}).items():
        conn.execute("INSERT INTO settings VALUES (?,?)", (k, v))
    for (vid, k), v in (vault_overrides or {}).items():
        conn.execute("INSERT INTO vault_settings VALUES (?,?,?)", (vid, k, v))
    conn.commit(); conn.close()
    return db

def test_schema_default_when_no_overrides(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(tmp_path)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id=None)
    assert r.get('embeddings:chunk_size') == 600

def test_global_db_overrides_default(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(tmp_path, global_overrides={'embeddings:chunk_size': '300'})
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id=None)
    assert r.get('embeddings:chunk_size') == '300'

def test_vault_setting_overrides_global(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(
        tmp_path,
        global_overrides={'embeddings:chunk_size': '300'},
        vault_overrides={('vault-1', 'embeddings:chunk_size'): '150'}
    )
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id='vault-1')
    assert r.get('embeddings:chunk_size') == '150'

def test_vault_setting_does_not_leak(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(
        tmp_path,
        vault_overrides={('vault-1', 'embeddings:chunk_size'): '150'}
    )
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id='vault-2')  # different vault
    assert r.get('embeddings:chunk_size') == 600  # schema default
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_settings_resolver.py -v
```
Expected: ImportError — `SettingsResolver` does not exist yet

**Step 3: Add `SettingsResolver` to `core/settings.py`**

Add this class *before* the `# --- Global Settings Singleton ---` line at the bottom:

```python
class SettingsResolver:
    """
    Vault-aware settings resolver. Lightweight — create per task invocation.
    Resolution chain: vault_settings → settings.db global → config.ini → schema default
    """

    def __init__(self, vault_id: str | None = None, _settings_obj: 'Settings | None' = None):
        self.vault_id = vault_id
        # Share the schema and config from the global singleton
        self._s = _settings_obj or settings

    def get(self, key: str):
        if ':' not in key:
            raise ValueError("Setting key must be 'section:key'")

        # Tier 1: vault-specific override
        if self.vault_id:
            try:
                from core.manager import get_settings_db_path, _connect
                with _connect(get_settings_db_path()) as conn:
                    row = conn.execute(
                        "SELECT value FROM vault_settings WHERE vault_id=? AND key=?",
                        (self.vault_id, key)
                    ).fetchone()
                    if row:
                        return row['value']
            except Exception:
                pass

        # Tiers 2–4: delegate to the global Settings object
        return self._s.get(key)
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_settings_resolver.py -v
```
Expected: 4 PASSED

**Step 5: Write `tests/rigs/check_settings_resolution.py`**

```python
#!/usr/bin/env python3
"""
check_settings_resolution.py — Validates 4-tier settings resolution chain.

Checks that SettingsResolver correctly prioritises:
  vault_settings > settings.db global > config.ini > schema default

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_settings_resolution.py
  python tests/rigs/check_settings_resolution.py --verbose
  python tests/rigs/check_settings_resolution.py --settings-db path/to/settings.db
"""
import argparse, sqlite3, sys, os, tempfile

GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'

def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False

def run_checks(settings_db_path, verbose):
    results = []

    # Inject a fresh test DB
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, 'settings.db')
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE vault_settings (vault_id TEXT, key TEXT, value TEXT, PRIMARY KEY(vault_id,key))")
        conn.execute("INSERT INTO settings VALUES ('embeddings:chunk_size','400')")
        conn.execute("INSERT INTO vault_settings VALUES ('v1','embeddings:chunk_size','200')")
        conn.commit(); conn.close()

        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
        import core.manager as m
        orig = m.get_settings_db_path
        m.get_settings_db_path = lambda: db

        try:
            from importlib import reload
            import core.settings as cs
            reload(cs)
            from core.settings import SettingsResolver

            # Schema default
            r = SettingsResolver(vault_id=None)
            val = r.get('embeddings:chunk_overlap')
            results.append(
                _pass(f"Schema default: embeddings:chunk_overlap = {val}") if val == 100
                else _fail(f"Schema default wrong: got {val!r}, expected 100")
            )

            # Global DB override
            val = r.get('embeddings:chunk_size')
            results.append(
                _pass(f"Global DB override: embeddings:chunk_size = {val}") if val == '400'
                else _fail(f"Global DB override wrong: got {val!r}, expected '400'")
            )

            # Vault override
            r2 = SettingsResolver(vault_id='v1')
            val = r2.get('embeddings:chunk_size')
            results.append(
                _pass(f"Vault override: embeddings:chunk_size for v1 = {val}") if val == '200'
                else _fail(f"Vault override wrong: got {val!r}, expected '200'")
            )

            # No leak to other vault
            r3 = SettingsResolver(vault_id='v2')
            val = r3.get('embeddings:chunk_size')
            results.append(
                _pass(f"No vault leak: v2 sees global '400' not v1's '200'") if val == '400'
                else _fail(f"Vault leak detected: v2 got {val!r}, expected '400'")
            )

        finally:
            m.get_settings_db_path = orig

    return results

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--settings-db', default='settings.db')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print("\nDocVault Layer 2 — Settings Resolution Check\n")
    results = run_checks(args.settings_db, args.verbose)
    failed = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
```

**Step 6: Run everything**

```bash
venv\Scripts\pytest tests/unit/test_settings_resolver.py -v
python tests/rigs/check_settings_resolution.py --verbose
```

**Step 7: Commit**

```bash
git add core/settings.py tests/unit/test_settings_resolver.py tests/rigs/check_settings_resolution.py
git commit -m "feat(layer2): add SettingsResolver — vault-aware 4-tier settings lookup"
```

---

### Task 2.3: Update worker scheduling — composite priority

**Files:**
- Modify: `core/manager.py`

**Step 1: Write test**

Create `tests/unit/test_worker_priority.py`:

```python
import sqlite3, time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _db(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db(); m.init_db(db)
    m.bootstrap_default_vault(db, 'E:/test')
    return m, db

def test_high_vault_priority_claimed_first(tmp_path, monkeypatch):
    m, db = _db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    # Insert two vaults
    conn.execute("INSERT INTO vaults VALUES ('v-high','High','E:/h',1,'#fff','active',NULL,NULL)")
    conn.execute("INSERT INTO vaults VALUES ('v-low', 'Low', 'E:/l',9,'#000','active',NULL,NULL)")
    # Two tasks, same extractor priority
    conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                 "VALUES ('h1','/h/f.pdf','pdf','PENDING',10,'v-high')")
    conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                 "VALUES ('l1','/l/f.pdf','pdf','PENDING',10,'v-low')")
    conn.commit(); conn.close()

    task = m.claim_pending_task(db, 'worker-1')
    assert task is not None
    assert task['vault_id'] == 'v-high'

def test_aging_raises_old_low_priority_task(tmp_path, monkeypatch):
    """A very old task should have a better effective priority than a new one."""
    m, db = _db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO vaults VALUES ('v1','V1','E:/v1',5,'#fff','active',NULL,NULL)")
    # Old task: created 2 hours ago, low vault priority
    conn.execute("""INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id,last_update)
                    VALUES ('old1','/old.pdf','pdf','PENDING',5,'v1',
                    datetime('now','-7200 seconds'))""")
    # New task: created now, same vault priority
    conn.execute("""INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id,last_update)
                    VALUES ('new1','/new.pdf','pdf','PENDING',5,'v1',
                    datetime('now'))""")
    conn.commit(); conn.close()

    task = m.claim_pending_task(db, 'worker-1')
    # The old task should be claimed first due to aging bonus
    assert task['file_hash'] == 'old1'
```

**Step 2: Run to confirm current behavior (new task might win or order may differ)**

```bash
venv\Scripts\pytest tests/unit/test_worker_priority.py -v
```

**Step 3: Update `claim_pending_task()` in `core/manager.py`**

Replace the existing `claim_pending_task` function:

```python
def claim_pending_task(db_path, worker_id):
    """
    Claim the highest-priority PENDING task using composite priority:
        effective_priority = (vault_priority * extractor_priority) - age_bonus
    where age_bonus = seconds_since_last_update / 3600
    Lower vault_priority = higher importance (priority 1 beats priority 9).
    Larger effective_priority value wins (ORDER BY DESC).
    """
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT t.file_hash, t.file_path, t.file_type, t.priority, t.vault_id,
                      COALESCE(v.priority, 5) AS vault_priority
               FROM tasks t
               LEFT JOIN vaults v ON t.vault_id = v.vault_id
               WHERE t.status = 'PENDING'
               ORDER BY
                 -- Lower vault_priority = more important → invert with (10 - vault_priority)
                 ((10 - COALESCE(v.priority, 5)) * COALESCE(t.priority, 10))
                 + (CAST(
                     (julianday('now') - julianday(t.last_update)) * 86400
                    AS REAL) / 3600.0)
                 DESC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        task = dict(row)
        conn.execute(
            """UPDATE tasks SET status = 'PROCESSING', worker_id = ?,
               last_update = CURRENT_TIMESTAMP WHERE file_hash = ?""",
            (worker_id, task['file_hash'])
        )
        conn.commit()
        task['status'] = 'PROCESSING'
        return task
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_worker_priority.py -v
```
Expected: 2 PASSED

**Step 5: Write `tests/rigs/check_worker_priority.py`**

```python
#!/usr/bin/env python3
"""
check_worker_priority.py — Validates composite priority task scheduling.

Inserts tasks with different vault priorities and ages, then checks
that claim_pending_task() returns them in the correct order.

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_worker_priority.py
  python tests/rigs/check_worker_priority.py --verbose
"""
import argparse, sqlite3, sys, os, tempfile

GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'
def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print("\nDocVault Layer 2 — Worker Priority Check\n")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    results = []

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, 'test.db')
        sdb = os.path.join(td, 'settings.db')
        import core.manager as m
        orig_db = m.get_db_path
        orig_sdb = m.get_settings_db_path
        m.get_db_path = lambda db_path=None: db
        m.get_settings_db_path = lambda: sdb
        try:
            m.init_settings_db(); m.init_db(db)
            conn = sqlite3.connect(db)
            conn.execute("INSERT INTO vaults VALUES ('vh','High','E:/h',1,'#f','active',NULL,NULL)")
            conn.execute("INSERT INTO vaults VALUES ('vl','Low', 'E:/l',9,'#0','active',NULL,NULL)")
            conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                         "VALUES ('h1','/h.pdf','pdf','PENDING',10,'vh')")
            conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                         "VALUES ('l1','/l.pdf','pdf','PENDING',10,'vl')")
            conn.commit(); conn.close()

            task = m.claim_pending_task(db, 'rig')
            results.append(
                _pass(f"High-priority vault task claimed first: {task['file_hash']}") if task and task['file_hash']=='h1'
                else _fail(f"Expected 'h1', got {task}")
            )

            # Reset and test aging
            conn = sqlite3.connect(db)
            conn.execute("UPDATE tasks SET status='PENDING', worker_id=NULL WHERE file_hash='h1'")
            conn.execute("UPDATE tasks SET status='PENDING', worker_id=NULL, "
                         "last_update=datetime('now','-7200 seconds') WHERE file_hash='l1'")
            conn.commit(); conn.close()

            task = m.claim_pending_task(db, 'rig')
            results.append(
                _pass(f"Aged low-priority task wins after 2h: {task['file_hash']}") if task and task['file_hash']=='l1'
                else _fail(f"Aging not working: expected 'l1', got {task}")
            )
        finally:
            m.get_db_path = orig_db
            m.get_settings_db_path = orig_sdb

    failed = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
```

**Step 6: Run rig**

```bash
python tests/rigs/check_worker_priority.py --verbose
```
Expected: 2 passed, exit 0

**Step 7: Commit**

```bash
git add core/manager.py tests/unit/test_worker_priority.py tests/rigs/check_worker_priority.py
git commit -m "feat(layer2): composite priority scheduling — vault_priority × extractor_priority + age_bonus"
```

---

### Task 2.4: Create resource governor `core/monitor.py`

**Files:**
- Create: `core/monitor.py`

**Step 1: Write tests**

Create `tests/unit/test_monitor.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from core.monitor import ThrottleStateMachine, MonitorReading, ThrottleThresholds

def _thresholds():
    return ThrottleThresholds()  # all defaults

def test_normal_to_throttled_on_gpu_temp():
    sm = ThrottleStateMachine()
    r = MonitorReading(gpu_temp=82.0)
    sm.update(r, _thresholds())
    assert sm.state == 'throttled'

def test_normal_to_cooldown_on_critical_gpu_temp():
    sm = ThrottleStateMachine()
    r = MonitorReading(gpu_temp=90.0)
    sm.update(r, _thresholds())
    assert sm.state == 'cooldown'

def test_normal_no_change_on_safe_readings():
    sm = ThrottleStateMachine()
    r = MonitorReading(gpu_temp=60.0, gpu_util=30.0, cpu_temp=50.0, ram_pct=40.0)
    sm.update(r, _thresholds())
    assert sm.state == 'normal'

def test_dummy_sensor_returns_zeros():
    from core.monitor import DummySensor
    s = DummySensor('test')
    r = s.read()
    assert r.gpu_temp == 0.0
    assert r.cpu_temp == 0.0

def test_dummy_sensor_never_raises():
    from core.monitor import DummySensor
    s = DummySensor('any')
    r = s.read()   # must not raise
    assert r is not None
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_monitor.py -v
```

**Step 3: Implement `core/monitor.py`**

```python
"""
core/monitor.py — Resource governor for DocVault.

Samples hardware metrics once per minute and adaptively throttles
the worker pool. Extendable sensor registry — add new GPU/CPU vendors
by subclassing BaseSensor.

Throttle states: normal → throttled → cooldown → (retest) → normal
"""

from __future__ import annotations
import threading
import time
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MonitorReading:
    """Combined hardware reading from all sensors."""
    cpu_pct:      float = 0.0
    cpu_temp:     float = 0.0
    ram_used_gb:  float = 0.0
    ram_total_gb: float = 0.0
    ram_pct:      float = 0.0
    gpu_temp:     float = 0.0
    gpu_util_pct: float = 0.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    sampled_at:   str = ''

    def __post_init__(self):
        if not self.sampled_at:
            self.sampled_at = datetime.now(timezone.utc).isoformat()


@dataclass
class ThrottleThresholds:
    """All thresholds configurable via settings. Defaults match design doc."""
    gpu_temp_throttle:  float = 80.0
    gpu_temp_cooldown:  float = 88.0
    gpu_util_throttle:  float = 70.0
    sustained_minutes:  int   = 3
    cooldown_minutes:   int   = 10
    ram_throttle_pct:   float = 85.0
    cpu_temp_throttle:  float = 85.0


# ---------------------------------------------------------------------------
# Sensor abstraction
# ---------------------------------------------------------------------------

class BaseSensor(ABC):
    """Abstract hardware sensor. Return zeros on failure; never raise."""

    def __init__(self, name: str):
        self.name = name
        self.available = False

    @abstractmethod
    def _init_hardware(self) -> bool:
        """Attempt hardware initialisation. Return True if successful."""
        ...

    @abstractmethod
    def _read_hardware(self) -> MonitorReading:
        """Read hardware metrics. Called only if available=True."""
        ...

    def initialise(self) -> bool:
        """Try to initialise. On failure, log warning — not an error."""
        try:
            self.available = self._init_hardware()
        except Exception as e:
            warnings.warn(f"[monitor] {self.name} sensor failed to init: {e} — using dummy")
            self.available = False
        return self.available

    def read(self) -> MonitorReading:
        if not self.available:
            return MonitorReading()
        try:
            return self._read_hardware()
        except Exception:
            return MonitorReading()


class DummySensor(BaseSensor):
    """Fallback sensor returning all zeros. Used when hardware sensor fails to init."""

    def _init_hardware(self) -> bool:
        return True  # dummy always available

    def _read_hardware(self) -> MonitorReading:
        return MonitorReading()


class NvidiaSensor(BaseSensor):
    """NVIDIA GPU sensor via pynvml."""

    def __init__(self):
        super().__init__('NvidiaSensor')
        self._handle = None

    def _init_hardware(self) -> bool:
        import pynvml
        pynvml.nvmlInit()
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return True

    def _read_hardware(self) -> MonitorReading:
        import pynvml
        temp     = pynvml.nvmlDeviceGetTemperature(self._handle, pynvml.NVML_TEMPERATURE_GPU)
        util     = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        return MonitorReading(
            gpu_temp      = float(temp),
            gpu_util_pct  = float(util.gpu),
            vram_used_gb  = mem_info.used  / 1024**3,
            vram_total_gb = mem_info.total / 1024**3,
        )


class PSUtilSensor(BaseSensor):
    """CPU utilization and RAM via psutil."""

    def __init__(self):
        super().__init__('PSUtilSensor')

    def _init_hardware(self) -> bool:
        import psutil  # noqa — just verify it imports
        return True

    def _read_hardware(self) -> MonitorReading:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=1)
        ram     = psutil.virtual_memory()
        return MonitorReading(
            cpu_pct      = cpu_pct,
            ram_used_gb  = ram.used  / 1024**3,
            ram_total_gb = ram.total / 1024**3,
            ram_pct      = ram.percent,
        )


class WMICPUTempSensor(BaseSensor):
    """CPU temperature via WMI (Windows only)."""

    def __init__(self):
        super().__init__('WMICPUTempSensor')

    def _init_hardware(self) -> bool:
        import wmi
        w = wmi.WMI(namespace='root/wmi')
        # Probe to check it works
        _ = w.MSAcpi_ThermalZoneTemperature()
        return True

    def _read_hardware(self) -> MonitorReading:
        import wmi
        w = wmi.WMI(namespace='root/wmi')
        temps = w.MSAcpi_ThermalZoneTemperature()
        if temps:
            # WMI returns temperature in tenths of Kelvin
            celsius = (temps[0].CurrentTemperature / 10.0) - 273.15
            return MonitorReading(cpu_temp=celsius)
        return MonitorReading()


def _merge_readings(*readings: MonitorReading) -> MonitorReading:
    """Merge non-zero fields from multiple readings into one."""
    merged = MonitorReading()
    for r in readings:
        if r.cpu_pct:      merged.cpu_pct      = r.cpu_pct
        if r.cpu_temp:     merged.cpu_temp      = r.cpu_temp
        if r.ram_pct:      merged.ram_pct       = r.ram_pct
        if r.ram_used_gb:  merged.ram_used_gb   = r.ram_used_gb
        if r.ram_total_gb: merged.ram_total_gb  = r.ram_total_gb
        if r.gpu_temp:     merged.gpu_temp      = r.gpu_temp
        if r.gpu_util_pct: merged.gpu_util_pct  = r.gpu_util_pct
        if r.vram_used_gb: merged.vram_used_gb  = r.vram_used_gb
        if r.vram_total_gb:merged.vram_total_gb = r.vram_total_gb
    return merged


# ---------------------------------------------------------------------------
# Throttle state machine
# ---------------------------------------------------------------------------

class ThrottleStateMachine:
    """
    Transitions:
        normal → throttled   (pressure detected)
        throttled → cooldown (pressure sustained > sustained_minutes)
        throttled → normal   (pressure clears)
        cooldown → normal    (after cooldown_minutes, if pressure gone)
        cooldown → cooldown  (pressure still present after cooldown)
        normal → cooldown    (critical threshold: gpu_temp > gpu_temp_cooldown)
    """

    def __init__(self):
        self.state: str = 'normal'
        self._throttled_since: Optional[float] = None
        self._cooldown_until:  Optional[float] = None

    def update(self, reading: MonitorReading, thresholds: ThrottleThresholds):
        now = time.monotonic()

        # Immediate cooldown on critical GPU temp
        if reading.gpu_temp > thresholds.gpu_temp_cooldown:
            self._enter_cooldown(now, thresholds)
            return

        # Any throttle trigger
        pressure = (
            reading.gpu_temp     > thresholds.gpu_temp_throttle or
            reading.gpu_util_pct > thresholds.gpu_util_throttle or
            reading.cpu_temp     > thresholds.cpu_temp_throttle or
            reading.ram_pct      > thresholds.ram_throttle_pct
        )

        if self.state == 'normal':
            if pressure:
                self.state = 'throttled'
                self._throttled_since = now

        elif self.state == 'throttled':
            if not pressure:
                self.state = 'normal'
                self._throttled_since = None
            elif self._throttled_since and \
                 (now - self._throttled_since) >= thresholds.sustained_minutes * 60:
                self._enter_cooldown(now, thresholds)

        elif self.state == 'cooldown':
            if self._cooldown_until and now >= self._cooldown_until:
                # Retest
                if pressure:
                    self._enter_cooldown(now, thresholds)  # extend cooldown
                else:
                    self.state = 'normal'
                    self._cooldown_until = None
                    self._throttled_since = None

    def _enter_cooldown(self, now: float, thresholds: ThrottleThresholds):
        self.state = 'cooldown'
        self._throttled_since = None
        self._cooldown_until = now + thresholds.cooldown_minutes * 60


# ---------------------------------------------------------------------------
# HardwareMonitor — the daemon
# ---------------------------------------------------------------------------

# Shared throttle state (read by workers)
_throttle_state: str = 'normal'
_throttle_lock = threading.Lock()
_last_reading: Optional[MonitorReading] = None


def get_throttle_state() -> str:
    with _throttle_lock:
        return _throttle_state


def get_last_reading() -> Optional[MonitorReading]:
    with _throttle_lock:
        return _last_reading


def _set_throttle_state(state: str, reading: MonitorReading):
    global _throttle_state, _last_reading
    with _throttle_lock:
        _throttle_state = state
        _last_reading   = reading


def _load_thresholds() -> ThrottleThresholds:
    """Read thresholds from settings at call time (not import time)."""
    try:
        from core.settings import settings as s
        return ThrottleThresholds(
            gpu_temp_throttle = float(s.get('monitor:gpu_temp_throttle')  or 80),
            gpu_temp_cooldown = float(s.get('monitor:gpu_temp_cooldown')  or 88),
            gpu_util_throttle = float(s.get('monitor:gpu_util_throttle')  or 70),
            sustained_minutes = int(  s.get('monitor:sustained_minutes')  or 3),
            cooldown_minutes  = int(  s.get('monitor:cooldown_minutes')   or 10),
            ram_throttle_pct  = float(s.get('monitor:ram_throttle_pct')   or 85),
            cpu_temp_throttle = float(s.get('monitor:cpu_temp_throttle')  or 85),
        )
    except Exception:
        return ThrottleThresholds()


def _record_sample(reading: MonitorReading, state: str):
    """Write sample to logs.db system_stats. Best-effort."""
    try:
        from core.manager import get_logs_db_path, _connect
        with _connect(get_logs_db_path()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO system_stats
                   (sampled_at, cpu_pct, cpu_temp, ram_used_gb, ram_total_gb, ram_pct,
                    gpu_temp, gpu_util_pct, vram_used_gb, vram_total_gb, throttle_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (reading.sampled_at, reading.cpu_pct, reading.cpu_temp,
                 reading.ram_used_gb, reading.ram_total_gb, reading.ram_pct,
                 reading.gpu_temp, reading.gpu_util_pct,
                 reading.vram_used_gb, reading.vram_total_gb, state)
            )
            conn.commit()
    except Exception:
        pass


class HardwareMonitor:
    """
    Daemon that samples hardware sensors on a configurable interval
    and maintains the shared throttle state read by workers.
    """

    def __init__(self):
        self._sm = ThrottleStateMachine()
        self._sensors: list[BaseSensor] = []
        self._init_sensors()

    def _init_sensors(self):
        """Attempt to initialise each sensor. Fall back to dummy on failure."""
        candidates = [PSUtilSensor(), NvidiaSensor(), WMICPUTempSensor()]
        for sensor in candidates:
            if sensor.initialise():
                self._sensors.append(sensor)
                print(f"[monitor] {sensor.name} initialised")
            else:
                dummy = DummySensor(f"Dummy({sensor.name})")
                dummy.initialise()
                self._sensors.append(dummy)
                print(f"[monitor] {sensor.name} unavailable — using dummy (zeros)")

    def _sample(self) -> MonitorReading:
        readings = [s.read() for s in self._sensors]
        return _merge_readings(*readings)

    def _get_interval(self) -> int:
        try:
            from core.settings import settings as s
            return int(s.get('monitor:sample_interval') or 60)
        except Exception:
            return 60

    def _is_enabled(self) -> bool:
        try:
            from core.settings import settings as s
            return str(s.get('monitor:enabled') or 'true').lower() != 'false'
        except Exception:
            return True

    def run(self):
        """Main daemon loop. Call from a daemon thread."""
        print("[monitor] Resource governor starting")
        while True:
            if self._is_enabled():
                reading    = self._sample()
                thresholds = _load_thresholds()
                self._sm.update(reading, thresholds)
                _set_throttle_state(self._sm.state, reading)
                _record_sample(reading, self._sm.state)
            time.sleep(self._get_interval())
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_monitor.py -v
```
Expected: 5 PASSED

**Step 5: Write `tests/rigs/check_resource_governor.py`**

```python
#!/usr/bin/env python3
"""
check_resource_governor.py — Validates sensor init and throttle state machine.

Uses injected mock readings to exercise all state transitions without
needing real hardware.

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_resource_governor.py
  python tests/rigs/check_resource_governor.py --verbose
"""
import argparse, sys, os

GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'
def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print("\nDocVault Layer 2 — Resource Governor Check\n")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

    from core.monitor import (ThrottleStateMachine, MonitorReading,
                               ThrottleThresholds, DummySensor)
    results = []
    t = ThrottleThresholds()

    # Dummy sensor
    d = DummySensor('test')
    d.initialise()
    r = d.read()
    results.append(_pass("DummySensor reads without raising") if r is not None
                   else _fail("DummySensor raised"))

    # Normal → throttled on GPU temp
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=82.0), t)
    results.append(_pass("Normal → Throttled on GPU temp 82°C") if sm.state == 'throttled'
                   else _fail(f"Expected throttled, got {sm.state}"))

    # Normal → cooldown on critical GPU temp
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=90.0), t)
    results.append(_pass("Normal → Cooldown on GPU temp 90°C") if sm.state == 'cooldown'
                   else _fail(f"Expected cooldown, got {sm.state}"))

    # Throttled → normal when pressure clears
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=82.0), t)
    sm.update(MonitorReading(gpu_temp=60.0), t)
    results.append(_pass("Throttled → Normal when pressure clears") if sm.state == 'normal'
                   else _fail(f"Expected normal, got {sm.state}"))

    # Normal stays normal on safe readings
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=50.0, gpu_util=20.0, cpu_temp=40.0, ram_pct=30.0), t)
    results.append(_pass("Normal stays Normal on safe readings") if sm.state == 'normal'
                   else _fail(f"Expected normal, got {sm.state}"))

    # RAM pressure
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(ram_pct=90.0), t)
    results.append(_pass("Normal → Throttled on RAM 90%") if sm.state == 'throttled'
                   else _fail(f"Expected throttled, got {sm.state}"))

    failed = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
```

**Step 6: Run everything**

```bash
venv\Scripts\pytest tests/unit/test_monitor.py -v
python tests/rigs/check_resource_governor.py --verbose
```
Expected: all PASS, exit 0

**Step 7: Commit**

```bash
git add core/monitor.py tests/unit/test_monitor.py tests/rigs/check_resource_governor.py
git commit -m "feat(layer2): resource governor — sensor abstraction, throttle state machine, HardwareMonitor daemon"
```

---

### Task 2.5: Wire governor and update workers to use ExtractorContext + logs.db

**Files:**
- Modify: `run.py`
- Modify: `workers/utils.py`
- Modify: `workers/extraction_worker.py`
- Modify: `workers/embedding_worker.py`

**Step 1: Add monitor daemon to `run.py`**

In `start()`, before the ingestion worker thread, add:

```python
    from core.monitor import HardwareMonitor
    monitor = HardwareMonitor()
    t_monitor = threading.Thread(target=monitor.run, daemon=True, name='monitor')
    t_monitor.start()
```

**Step 2: Update `workers/utils.py`** — add throttle-awareness

Replace the entire file:

```python
import time
from core import manager


def interruptible_sleep(db_path, duration, step=1):
    """Sleep duration seconds, waking early if paused."""
    slept = 0
    while slept < duration:
        if manager.get_pause_state():
            break
        time.sleep(step)
        slept += step


def should_pause_or_throttle() -> tuple[bool, str]:
    """
    Returns (should_skip, reason).
    Workers check this before claiming each task.
    """
    if manager.get_pause_state():
        return True, 'paused'
    try:
        from core.monitor import get_throttle_state
        state = get_throttle_state()
        if state == 'cooldown':
            return True, 'cooldown'
        return False, state
    except Exception:
        return False, 'normal'


def get_throttle_sleep(state: str) -> int:
    """Extra sleep between tasks based on throttle state."""
    return {'normal': 0, 'throttled': 5, 'cooldown': 30}.get(state, 0)
```

**Step 3: Update `workers/extraction_worker.py`** — use ExtractorContext + logs.db

Replace `process_task()` and update `run()`:

```python
import os, socket, time, threading
from core import manager, router
from core.extractors.base import LegacyExtractorAdapter, ExtractorContext, ExtractorLogger
from core.settings import SettingsResolver
from extractors import image_extractor, unknown_extractor
from workers.utils import interruptible_sleep, should_pause_or_throttle, get_throttle_sleep


def _build_context(task: dict) -> ExtractorContext:
    vault_id  = task.get('vault_id') or ''
    file_hash = task['file_hash']
    return ExtractorContext(
        vault_id     = vault_id,
        file_hash    = file_hash,
        cancel_token = threading.Event(),
        logger       = ExtractorLogger('extraction_worker', vault_id, file_hash),
        settings     = SettingsResolver(vault_id=vault_id),
        timeout_secs = None,
    )


def process_task(db_path, task):
    file_hash = task['file_hash']
    file_path = task['file_path']
    file_type = task['file_type'] or ''

    ctx = _build_context(task)
    ctx.logger.info(f"Starting extraction: {os.path.basename(file_path)}")

    extractors = router.get_extractors(file_type)

    if extractors == [unknown_extractor]:
        _, msg = unknown_extractor.extract(file_path)
        manager.complete_extraction(db_path, file_hash, status='UNKNOWN', error=msg)
        ctx.logger.info(f"Flagged as UNKNOWN: {file_type}")
        return

    errors, combined_text, combined_metadata = [], [], {}
    combined_images = []

    for ext_module in extractors:
        adapter = LegacyExtractorAdapter(ext_module)
        result  = adapter.run(file_path, ctx)

        if result.status == 'cancelled':
            ctx.logger.warning(f"Extractor {adapter.name} cancelled")
            manager.complete_extraction(db_path, file_hash, status='PENDING', error=None)
            return

        for e in result.errors:
            errors.append(f"[{e.extractor_name}] {e.message}")

        if result.text:
            combined_text.append(result.text)
        if result.metadata:
            combined_metadata.update(result.metadata)
        for img in result.images:
            manager.insert_extracted_image(db_path, file_hash, {
                'file_path':   img.file_path,
                'page_num':    img.page_num,
                'image_index': img.image_index,
                'width':       img.width,
                'height':      img.height,
            })
            combined_images.append(img)

        # Log timing to logs.db
        _record_timing(task, adapter.name, result.elapsed_secs)

    final_text   = "\n\n".join(combined_text) if combined_text else None
    has_content  = bool(final_text or combined_metadata)
    final_status = 'ERROR' if (errors and not has_content) else 'EXTRACTED'

    manager.complete_extraction(
        db_path, file_hash, status=final_status,
        text=final_text,
        metadata=combined_metadata if combined_metadata else None,
        error=' | '.join(errors) if errors else None,
    )
    ctx.logger.info(f"Extraction complete: {final_status}")


def _record_timing(task: dict, extractor_name: str, elapsed: float):
    """Best-effort write to logs.db task_timings."""
    try:
        from datetime import datetime, timezone
        from core.manager import get_logs_db_path, _connect
        with _connect(get_logs_db_path()) as conn:
            conn.execute(
                """INSERT INTO task_timings
                   (file_hash, vault_id, extractor, file_size, elapsed_secs, completed_at)
                   VALUES (?,?,?,?,?,?)""",
                (task['file_hash'], task.get('vault_id'), extractor_name,
                 task.get('file_size'), elapsed,
                 datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
    except Exception:
        pass


def run(db_path, worker_id=None, shutdown_event=None):
    if worker_id is None:
        worker_id = f"extract-{socket.gethostname()}-{os.getpid()}"
    print(f"Extraction worker starting. ID: {worker_id}")

    while True:
        skip, reason = should_pause_or_throttle()
        if skip:
            print(f"Extraction worker {reason}. Sleeping...")
            interruptible_sleep(db_path, 10)
            continue

        extra = get_throttle_sleep(reason)
        if extra:
            time.sleep(extra)

        task = manager.claim_pending_task(db_path, worker_id)
        if task:
            try:
                process_task(db_path, task)
            except Exception as e:
                print(f"[ERROR] Extraction worker unhandled: {e}")
                manager.complete_extraction(db_path, task['file_hash'],
                                            status='ERROR', error=str(e))
        else:
            interruptible_sleep(db_path, 10)
```

**Step 4: Update `workers/embedding_worker.py`** — use vault-aware settings + logs.db

Replace `_load_vector_store()` and `process_task()` preamble only (preserve embedding logic):

```python
def _load_vector_store(vault_id: str | None = None):
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id=vault_id)
    return VectorStore(
        host=r.get('qdrant:host'),
        port=int(r.get('qdrant:port')),
        collection='docvault',
    )
```

Also wrap the start of `process_task`:
```python
def process_task(db_path, task, vs):
    file_hash = task['file_hash']
    file_path = task['file_path']
    vault_id  = task.get('vault_id')
    text      = task.get('extracted_text') or ''

    from core.settings import SettingsResolver
    from core.extractors.base import ExtractorLogger
    r      = SettingsResolver(vault_id=vault_id)
    logger = ExtractorLogger('embedding_worker', vault_id or '', file_hash)
    logger.info(f"Embedding: {os.path.basename(file_path)}")

    chunk_size    = int(r.get('embeddings:chunk_size')    or 600)
    chunk_overlap = int(r.get('embeddings:chunk_overlap') or 100)
    # ... rest unchanged, but replace print() calls with logger.info/error
```

Replace `should_pause_or_throttle` check in `run()`:
```python
        skip, reason = should_pause_or_throttle()
        if skip:
            print(f"Embedding worker {reason}. Sleeping...")
            interruptible_sleep(db_path, 10)
            continue
```

**Step 5: Start server, verify no errors**

```bash
venv\Scripts\python run.py
```
Watch for `[monitor]` lines. Expected:
```
[monitor] PSUtilSensor initialised
[monitor] NvidiaSensor initialised
[monitor] WMICPUTempSensor initialised  (or: unavailable — using dummy)
[monitor] Resource governor starting
```
Stop with Ctrl+C.

**Step 6: Commit**

```bash
git add run.py workers/utils.py workers/extraction_worker.py workers/embedding_worker.py
git commit -m "feat(layer2): wire governor + ExtractorContext into workers; replace print() with structured logging"
```

**Layer 2 complete. Gate: all three rigs exit 0.**

```bash
python tests/rigs/check_settings_resolution.py
python tests/rigs/check_resource_governor.py
python tests/rigs/check_worker_priority.py
```

---

## Layer 3 — Vault API and VaultManager

**Gate:** `python tests/rigs/check_vault_api.py` exits 0

---

### Task 3.1: Create `core/vault_manager.py`

**Files:**
- Create: `core/vault_manager.py`

**Step 1: Write tests**

Create `tests/unit/test_vault_manager.py`:

```python
import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _dbs(tmp_path, monkeypatch):
    import core.manager as m
    db  = str(tmp_path / 'docvault.db')
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_db_path',          lambda db_path=None: db)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db(); m.init_db(db)
    return db, sdb

def test_create_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager
    vm = VaultManager(db)
    v  = vm.create_vault('Art', 'E:/Art')
    assert v['name'] == 'Art'
    assert v['state'] == 'active'
    assert v['vault_id']

def test_list_vaults(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager
    vm = VaultManager(db)
    vm.create_vault('A', 'E:/A'); vm.create_vault('B', 'E:/B')
    vaults = vm.list_vaults()
    assert len(vaults) == 2

def test_archive_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager
    vm = VaultManager(db)
    v  = vm.create_vault('Test', 'E:/Test')
    vm.transition(v['vault_id'], 'archived')
    updated = vm.get_vault(v['vault_id'])
    assert updated['state'] == 'archived'

def test_cannot_gut_active_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager, VaultStateError
    vm = VaultManager(db)
    v  = vm.create_vault('Test', 'E:/Test')
    try:
        vm.transition(v['vault_id'], 'gutted')
        assert False, "Should have raised"
    except VaultStateError:
        pass

def test_cannot_delete_archived_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager, VaultStateError
    vm = VaultManager(db)
    v  = vm.create_vault('Test', 'E:/Test')
    vm.transition(v['vault_id'], 'archived')
    try:
        vm.transition(v['vault_id'], 'deleted')
        assert False, "Should have raised"
    except VaultStateError:
        pass

def test_duplicate_scan_directory_rejected(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager, VaultConflictError
    vm = VaultManager(db)
    vm.create_vault('A', 'E:/Shared')
    try:
        vm.create_vault('B', 'E:/Shared')
        assert False, "Should have raised"
    except VaultConflictError:
        pass
```

**Step 2: Run to confirm failure**

```bash
venv\Scripts\pytest tests/unit/test_vault_manager.py -v
```
Expected: ImportError — `core.vault_manager` does not exist

**Step 3: Implement `core/vault_manager.py`**

```python
"""
core/vault_manager.py — Vault CRUD, state machine, and settings resolution.

Vault state machine (API-enforced):
    active → archived → gutted → deleted
    archived → active  (restore)

Source files on disk are NEVER touched.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from core.manager import _connect, get_db_path


class VaultStateError(Exception):
    """Raised when a state transition is invalid."""


class VaultConflictError(Exception):
    """Raised when a vault constraint is violated (e.g. duplicate scan_directory)."""


# Valid transitions: from_state → [allowed to_states]
_TRANSITIONS = {
    'active':   ['archived'],
    'archived': ['active', 'gutted'],
    'gutted':   ['deleted'],
    'deleted':  [],
}


class VaultManager:

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def list_vaults(self, include_deleted: bool = False) -> list[dict]:
        with _connect(self.db_path) as conn:
            if include_deleted:
                rows = conn.execute("SELECT * FROM vaults ORDER BY name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM vaults WHERE state != 'deleted' ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_vault(self, vault_id: str) -> dict | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM vaults WHERE vault_id = ?", (vault_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_vault(self, name: str, scan_directory: str,
                     priority: int = 5, color: str = '#6366f1') -> dict:
        # Guard against duplicate scan directories
        with _connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT vault_id FROM vaults WHERE scan_directory = ? AND state != 'deleted'",
                (scan_directory,)
            ).fetchone()
            if existing:
                raise VaultConflictError(
                    f"A vault already watches '{scan_directory}'"
                )

            vault_id = str(uuid.uuid4())
            now      = self._now()
            conn.execute(
                """INSERT INTO vaults
                   (vault_id, name, scan_directory, priority, color, state, created_at, updated_at)
                   VALUES (?,?,?,?,?,'active',?,?)""",
                (vault_id, name, scan_directory, priority, color, now, now)
            )
            conn.commit()
        return self.get_vault(vault_id)

    def update_vault(self, vault_id: str, **fields) -> dict:
        allowed = {'name', 'scan_directory', 'priority', 'color'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_vault(vault_id)
        updates['updated_at'] = self._now()
        set_clause = ', '.join(f"{k} = ?" for k in updates)
        with _connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE vaults SET {set_clause} WHERE vault_id = ?",
                list(updates.values()) + [vault_id]
            )
            conn.commit()
        return self.get_vault(vault_id)

    def transition(self, vault_id: str, new_state: str) -> dict:
        vault = self.get_vault(vault_id)
        if not vault:
            raise VaultStateError(f"Vault {vault_id} not found")

        current = vault['state']
        allowed = _TRANSITIONS.get(current, [])
        if new_state not in allowed:
            raise VaultStateError(
                f"Cannot transition vault from '{current}' to '{new_state}'. "
                f"Allowed: {allowed}"
            )

        if new_state == 'gutted':
            self._gut_vault(vault_id)

        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE vaults SET state=?, updated_at=? WHERE vault_id=?",
                (new_state, self._now(), vault_id)
            )
            conn.commit()
        return self.get_vault(vault_id)

    def _gut_vault(self, vault_id: str):
        """
        Wipe all extracted content for this vault.
        Deletes: task records, FTS entries, extracted_images.
        Resets Qdrant vectors for this vault.
        Does NOT touch source files on disk.
        """
        with _connect(self.db_path) as conn:
            # Get all file_hashes for this vault
            hashes = [r[0] for r in conn.execute(
                "SELECT file_hash FROM tasks WHERE vault_id = ?", (vault_id,)
            ).fetchall()]

            # Remove from FTS
            for h in hashes:
                conn.execute("DELETE FROM fts_index WHERE file_hash = ?", (h,))

            # Remove extracted images
            conn.execute("DELETE FROM extracted_images WHERE source_hash IN "
                         f"({','.join('?' for _ in hashes)})", hashes)

            # Delete tasks
            conn.execute("DELETE FROM tasks WHERE vault_id = ?", (vault_id,))
            conn.commit()

        # Remove Qdrant vectors for this vault
        try:
            from embeddings.vector_store import VectorStore
            from core.settings import settings
            vs = VectorStore(
                host=settings.get('qdrant:host'),
                port=int(settings.get('qdrant:port')),
                collection='docvault',
            )
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            vs.client.delete(
                collection_name='docvault',
                points_selector=Filter(must=[
                    FieldCondition(key='vault_id', match=MatchValue(value=vault_id))
                ])
            )
        except Exception as e:
            print(f"[vault_manager] Warning: could not remove Qdrant vectors: {e}")

    def restore(self, vault_id: str) -> dict:
        """Convenience: archived → active."""
        return self.transition(vault_id, 'active')

    def reindex(self, vault_id: str):
        """Wipe vectors and reset COMPLETED tasks to EXTRACTED for this vault only."""
        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='EXTRACTED' WHERE vault_id=? AND status='COMPLETED'",
                (vault_id,)
            )
            conn.commit()
        # Remove Qdrant vectors
        try:
            from embeddings.vector_store import VectorStore
            from core.settings import settings
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            vs = VectorStore(
                host=settings.get('qdrant:host'),
                port=int(settings.get('qdrant:port')),
                collection='docvault',
            )
            vs.client.delete(
                collection_name='docvault',
                points_selector=Filter(must=[
                    FieldCondition(key='vault_id', match=MatchValue(value=vault_id))
                ])
            )
        except Exception as e:
            print(f"[vault_manager] Warning: reindex Qdrant removal failed: {e}")
```

**Step 4: Run tests**

```bash
venv\Scripts\pytest tests/unit/test_vault_manager.py -v
```
Expected: 6 PASSED

**Step 5: Commit**

```bash
git add core/vault_manager.py tests/unit/test_vault_manager.py
git commit -m "feat(layer3): VaultManager — CRUD, state machine, gut/reindex logic"
```

---

### Task 3.2: Create vault and monitor API routes

**Files:**
- Create: `api/routes/vaults.py`
- Create: `api/routes/monitor.py`
- Modify: `api/main.py`

**Step 1: Create `api/routes/vaults.py`**

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.vault_manager import VaultManager, VaultStateError, VaultConflictError
from core.manager import get_db_path

router = APIRouter(prefix="/api/vaults", tags=["vaults"])


class VaultCreate(BaseModel):
    name: str
    scan_directory: str
    priority: int = 5
    color: str = '#6366f1'


class VaultUpdate(BaseModel):
    name: str | None = None
    scan_directory: str | None = None
    priority: int | None = None
    color: str | None = None


class VaultSettingSet(BaseModel):
    value: str


def _vm():
    return VaultManager(get_db_path())


@router.get("")
def list_vaults():
    return _vm().list_vaults()


@router.post("")
def create_vault(body: VaultCreate):
    try:
        return _vm().create_vault(body.name, body.scan_directory,
                                  body.priority, body.color)
    except VaultConflictError as e:
        raise HTTPException(409, str(e))


@router.get("/{vault_id}")
def get_vault(vault_id: str):
    v = _vm().get_vault(vault_id)
    if not v:
        raise HTTPException(404, "Vault not found")
    return v


@router.put("/{vault_id}")
def update_vault(vault_id: str, body: VaultUpdate):
    return _vm().update_vault(vault_id, **body.model_dump(exclude_none=True))


@router.delete("/{vault_id}")
def delete_vault(vault_id: str, mode: str = 'archive'):
    state_map = {'archive': 'archived', 'gut': 'gutted', 'delete': 'deleted'}
    new_state = state_map.get(mode)
    if not new_state:
        raise HTTPException(400, f"mode must be one of: {list(state_map)}")
    try:
        return _vm().transition(vault_id, new_state)
    except VaultStateError as e:
        raise HTTPException(409, str(e))


@router.post("/{vault_id}/restore")
def restore_vault(vault_id: str):
    try:
        return _vm().restore(vault_id)
    except VaultStateError as e:
        raise HTTPException(409, str(e))


@router.post("/{vault_id}/reindex")
def reindex_vault(vault_id: str):
    _vm().reindex(vault_id)
    return {"ok": True}


@router.get("/{vault_id}/settings")
def get_vault_settings(vault_id: str):
    from core.settings import SettingsResolver, settings as gs
    r = SettingsResolver(vault_id=vault_id)
    return [
        {**meta, 'key': key, 'value': r.get(key)}
        for key, meta in gs.schema.items()
    ]


@router.post("/{vault_id}/settings")
def set_vault_setting(vault_id: str, key: str, body: VaultSettingSet):
    from core.manager import get_settings_db_path, _connect
    if key not in __import__('core.settings', fromlist=['settings']).settings.schema:
        raise HTTPException(400, f"Unknown setting key: {key}")
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vault_settings (vault_id, key, value) VALUES (?,?,?)",
            (vault_id, key, body.value)
        )
        conn.commit()
    return {"ok": True}


@router.delete("/{vault_id}/settings/{key}")
def delete_vault_setting(vault_id: str, key: str):
    from core.manager import get_settings_db_path, _connect
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "DELETE FROM vault_settings WHERE vault_id=? AND key=?",
            (vault_id, key)
        )
        conn.commit()
    return {"ok": True}
```

**Step 2: Create `api/routes/monitor.py`**

```python
from fastapi import APIRouter
from core.monitor import get_throttle_state, get_last_reading

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/status")
def monitor_status():
    reading = get_last_reading()
    state   = get_throttle_state()
    if reading is None:
        return {"state": state, "available": False}
    return {
        "state":         state,
        "available":     True,
        "cpu_pct":       reading.cpu_pct,
        "cpu_temp":      reading.cpu_temp,
        "ram_used_gb":   reading.ram_used_gb,
        "ram_total_gb":  reading.ram_total_gb,
        "ram_pct":       reading.ram_pct,
        "gpu_temp":      reading.gpu_temp,
        "gpu_util_pct":  reading.gpu_util_pct,
        "vram_used_gb":  reading.vram_used_gb,
        "vram_total_gb": reading.vram_total_gb,
        "sampled_at":    reading.sampled_at,
    }
```

**Step 3: Mount routes in `api/main.py`**

Find the section where other routers are mounted (look for `app.include_router`) and add:

```python
from api.routes.vaults  import router as vaults_router
from api.routes.monitor import router as monitor_router

app.include_router(vaults_router)
app.include_router(monitor_router)
```

**Step 4: Restart server and quick smoke test**

```bash
venv\Scripts\python run.py &
# wait ~3 seconds
curl http://localhost:8000/api/vaults
curl http://localhost:8000/api/monitor/status
```
Expected: JSON response for both. Stop server.

**Step 5: Commit**

```bash
git add api/routes/vaults.py api/routes/monitor.py api/main.py
git commit -m "feat(layer3): vault and monitor API routes"
```

---

### Task 3.3: Update ingestor for multi-vault scanning

**Files:**
- Modify: `core/ingestor.py`
- Modify: `run.py`

**Step 1: Read `core/ingestor.py` to understand current structure, then update `insert_task` calls to include `vault_id`**

In `core/ingestor.py`, the `ingest()` function calls `manager.insert_task(...)`. Add `vault_id` parameter:

Find every call to `manager.insert_task(db_path, file_hash, file_path, file_type, ...)` and add `vault_id=vault_id` as a keyword argument. The function signature already accepts it (nullable column).

Add `vault_id: str | None = None` parameter to the `ingest()` function signature.

**Step 2: Update `manager.insert_task()` to accept `vault_id`**

In `core/manager.py`, update `insert_task()`:

```python
def insert_task(db_path, file_hash, file_path, file_type, priority=10,
                file_size=None, file_created=None, file_modified=None,
                vault_id=None):
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO tasks
               (file_hash, file_path, file_type, priority, file_size,
                file_created, file_modified, vault_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_hash, file_path, file_type, priority, file_size,
             file_created, file_modified, vault_id)
        )
        conn.commit()
```

**Step 3: Update `run.py` ingestion worker to pass `vault_id`**

Replace the ingestion worker loop body to look up the vault for the scan directory:

```python
def ingestion_worker_run(db_path, interval_seconds=60):
    print(f"Ingestion worker starting. Will scan every {interval_seconds}s.")
    while True:
        try:
            from core.vault_manager import VaultManager
            vm = VaultManager(db_path)
            vaults = vm.list_vaults()
            active = [v for v in vaults if v['state'] == 'active']
            for vault in active:
                scan_dir = vault['scan_directory']
                vault_id = vault['vault_id']
                print(f"Scanning vault '{vault['name']}': {scan_dir}")
                ingestor.ingest(scan_dir, db_path, vault_id=vault_id)
        except Exception as e:
            print(f"[ERROR] Ingestion worker failed: {e}")
        time.sleep(interval_seconds)
```

**Step 4: Start server, confirm multiple vault scan paths work**

```bash
venv\Scripts\python run.py
```
Watch for `Scanning vault 'Documents': ...` in output. Stop with Ctrl+C.

**Step 5: Commit**

```bash
git add core/ingestor.py core/manager.py run.py
git commit -m "feat(layer3): multi-vault scanning — ingestor now iterates active vaults"
```

---

### Task 3.4: Write and run `tests/rigs/check_vault_api.py`

**Files:**
- Create: `tests/rigs/check_vault_api.py`

**Step 1: Write the rig**

```python
#!/usr/bin/env python3
"""
check_vault_api.py — Validates the vault API contract over HTTP.

Requires the DocVault server to be running at the target URL.
Tests: vault CRUD, state machine transitions, per-vault settings,
invalid transition rejection, duplicate directory rejection.

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_vault_api.py
  python tests/rigs/check_vault_api.py --url http://localhost:8000
  python tests/rigs/check_vault_api.py --verbose
"""
import argparse, sys, json
try:
    import urllib.request, urllib.error
except ImportError:
    pass

GREEN = '\033[92m'; RED = '\033[91m'; YELLOW = '\033[93m'; RESET = '\033[0m'
def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False
def _warn(msg): print(f"{YELLOW}WARN{RESET}  {msg}")


def _req(url, method='GET', body=None, params=None):
    if params:
        url += '?' + '&'.join(f"{k}={v}" for k, v in params.items())
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {'error': str(e)}


def run_checks(base_url, verbose):
    results = []
    vapi = f"{base_url}/api/vaults"

    # --- Create vault ---
    status, body = _req(vapi, 'POST', {'name': 'RigTest', 'scan_directory': 'E:/RigTest99'})
    results.append(_pass(f"POST /api/vaults → {status}") if status == 200
                   else _fail(f"Create vault failed: {status} {body}"))
    if status != 200:
        return results
    vault_id = body.get('vault_id')

    # --- Get vault ---
    status, body = _req(f"{vapi}/{vault_id}")
    results.append(_pass(f"GET /api/vaults/{{id}} → {status}") if status == 200
                   else _fail(f"Get vault: {status} {body}"))

    # --- List vaults ---
    status, body = _req(vapi)
    results.append(_pass(f"GET /api/vaults → {status}, {len(body)} vault(s)")
                   if status == 200 and isinstance(body, list)
                   else _fail(f"List vaults: {status}"))

    # --- Duplicate scan_directory rejected ---
    status, body = _req(vapi, 'POST', {'name': 'Dup', 'scan_directory': 'E:/RigTest99'})
    results.append(_pass("Duplicate scan_directory → 409") if status == 409
                   else _fail(f"Expected 409 for duplicate dir, got {status}"))

    # --- Cannot gut active vault ---
    status, body = _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'gut'})
    results.append(_pass("Gut active vault → 409") if status == 409
                   else _fail(f"Expected 409 gut active, got {status} {body}"))

    # --- Archive vault ---
    status, body = _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'archive'})
    results.append(_pass(f"Archive vault → {status}, state={body.get('state')}")
                   if status == 200 and body.get('state') == 'archived'
                   else _fail(f"Archive failed: {status} {body}"))

    # --- Cannot delete archived vault (must gut first) ---
    status, body = _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'delete'})
    results.append(_pass("Delete archived vault → 409") if status == 409
                   else _fail(f"Expected 409 delete archived, got {status} {body}"))

    # --- Restore vault ---
    status, body = _req(f"{vapi}/{vault_id}/restore", 'POST')
    results.append(_pass(f"Restore vault → {status}, state={body.get('state')}")
                   if status == 200 and body.get('state') == 'active'
                   else _fail(f"Restore failed: {status} {body}"))

    # --- Per-vault settings set/get ---
    status, body = _req(
        f"{vapi}/{vault_id}/settings", 'POST',
        body={'value': '300'},
        params={'key': 'embeddings:chunk_size'}
    )
    results.append(_pass(f"Set vault setting → {status}") if status == 200
                   else _fail(f"Set setting failed: {status} {body}"))

    status, settings_list = _req(f"{vapi}/{vault_id}/settings")
    vault_chunk = next((s for s in settings_list if s['key'] == 'embeddings:chunk_size'), None)
    results.append(_pass(f"Vault setting resolves to '300': {vault_chunk and vault_chunk.get('value')}")
                   if vault_chunk and vault_chunk.get('value') == '300'
                   else _fail(f"Setting not found or wrong value: {vault_chunk}"))

    # --- Monitor status ---
    status, body = _req(f"{base_url}/api/monitor/status")
    results.append(_pass(f"GET /api/monitor/status → {status}, state={body.get('state')}")
                   if status == 200 and 'state' in body
                   else _fail(f"Monitor status: {status} {body}"))

    # --- Clean up: gut then delete the test vault ---
    _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'archive'})
    _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'gut'})
    _req(f"{vapi}/{vault_id}", 'DELETE', params={'mode': 'delete'})
    if verbose:
        print("       Test vault cleaned up.")

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url',     default='http://localhost:8000',
                        help='DocVault server URL (default: http://localhost:8000)')
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print(f"\nDocVault Layer 3 — Vault API Check ({args.url})\n")
    print("NOTE: Server must be running. Start with: python run.py\n")

    results = run_checks(args.url, args.verbose)
    failed  = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
```

**Step 2: Start server and run rig**

```bash
venv\Scripts\python run.py
# In a second terminal:
python tests/rigs/check_vault_api.py --verbose
```
Expected: all PASS, exit 0. Stop server.

**Step 3: Commit**

```bash
git add tests/rigs/check_vault_api.py
git commit -m "test(layer3): add check_vault_api.py rig — Layer 3 gate"
```

**Layer 3 complete. Gate: `python tests/rigs/check_vault_api.py` exits 0 (with server running).**

---

## Layer 4 — Full UI

**Gate:** Manual validation — all pages load, vault operations work end-to-end.

---

### Task 4.1: Create `tests/rigs/README.md`

**Files:**
- Create: `tests/rigs/README.md`

```markdown
# DocVault Test Rigs

Standalone CLI diagnostic scripts. Each rig validates one layer of the
infrastructure migration. Exit 0 = all checks passed, 1 = failure.

## Usage

All rigs support `--help`, `--verbose`, and path overrides.

```bash
python tests/rigs/<rig>.py --help
python tests/rigs/<rig>.py --verbose
```

## Rigs

| Script | Layer | Gate | Requires server? |
|---|---|---|---|
| `check_db_migration.py` | 1 | DB schema, vaults, logs.db | No |
| `check_settings_resolution.py` | 2 | 4-tier settings chain | No |
| `check_resource_governor.py` | 2 | Sensor init, throttle state machine | No |
| `check_worker_priority.py` | 2 | Composite priority, aging | No |
| `check_vault_api.py` | 3 | Full vault CRUD + state machine | **Yes** |

## Run all offline rigs

```bash
python tests/rigs/check_db_migration.py && \
python tests/rigs/check_settings_resolution.py && \
python tests/rigs/check_resource_governor.py && \
python tests/rigs/check_worker_priority.py && \
echo "All offline checks passed"
```

## Run Layer 3 rig (server must be running)

```bash
python run.py &
python tests/rigs/check_vault_api.py --verbose
```
```

Commit:
```bash
git add tests/rigs/README.md
git commit -m "docs: add tests/rigs/README.md"
```

---

### Task 4.2: Update navigation on all pages

**Files:**
- Modify: `frontend/index.html`, `frontend/catalog.html`, `frontend/search.html`, `frontend/utils.html`, `frontend/settings.html`

In each file, find the `<nav>` block and replace with:

```html
<nav class="...existing classes...">
  <a href="/"         class="nav-link">Search</a>
  <a href="/status"   class="nav-link">Vault Status</a>
  <a href="/catalog"  class="nav-link">Vault Log</a>
  <a href="/utils"    class="nav-link">Utilities</a>
  <a href="/settings" class="nav-link">Settings</a>
</nav>
```

Add the `/status` route to `api/main.py` (alongside existing page routes):

```python
@app.get("/status", response_class=HTMLResponse)
async def status_page():
    return FileResponse("frontend/index.html")
```

Commit:
```bash
git add frontend/ api/main.py
git commit -m "feat(layer4): update nav — Search, Vault Status, Vault Log, Utilities, Settings"
```

---

### Task 4.3: Rewrite `frontend/index.html` → Vault Status page

**Files:**
- Modify: `frontend/index.html`

**What to build:**

1. **Resource strip** at top — polls `GET /api/monitor/status` every 10s:
   ```
   CPU 34°C  42%  |  RAM 28 GB / 128 GB  |  GPU 61°C  12%  ● Normal
   ```
   Dot colour: green (`#22c55e`) = normal, amber (`#f59e0b`) = throttled, red (`#ef4444`) = cooldown.

2. **Per-vault cards** — from `GET /api/vaults`, one card per vault showing:
   - Name, scan_directory, state badge
   - File counts by status (from existing `GET /api/workers` stats, filtered by vault_id)
   - Pause/Resume button (existing), Archive button (calls DELETE ?mode=archive)

3. **Stuck-task reset button** — preserve existing functionality.

Key JS functions to add:

```javascript
async function loadResourceStrip() {
  const data = await api('/api/monitor/status');
  const dot  = {normal:'🟢', throttled:'🟡', cooldown:'🔴'}[data.state] || '⚪';
  document.getElementById('resource-strip').textContent =
    `CPU ${data.cpu_temp?.toFixed(0)}°C  ${data.cpu_pct?.toFixed(0)}%  |  ` +
    `RAM ${data.ram_used_gb?.toFixed(1)} / ${data.ram_total_gb?.toFixed(0)} GB  |  ` +
    `GPU ${data.gpu_temp?.toFixed(0)}°C  ${data.gpu_util_pct?.toFixed(0)}%  ${dot}  ${data.state}`;
}

async function loadVaultCards() {
  const vaults = await api('/api/vaults');
  // render one card per vault
}
```

Commit:
```bash
git add frontend/index.html
git commit -m "feat(layer4): Vault Status page — resource strip and per-vault cards"
```

---

### Task 4.4: Update `frontend/catalog.html` → Vault Log

**Files:**
- Modify: `frontend/catalog.html`

Changes:
1. Update `<title>` and `<h1>` to "Vault Log"
2. Add vault filter dropdown above the file table — `GET /api/vaults` populates options; "All Vaults" is default
3. Pass `vault_id` filter to `GET /api/catalog` (add this query param support to `api/routes/catalog.py` and `core/manager.py list_tasks()`)

In `core/manager.py`, update `list_tasks()` to accept `vault_id`:
```python
def list_tasks(db_path, status=None, file_type=None, vault_id=None, limit=50, offset=0, ...):
    ...
    if vault_id:
        where.append("vault_id = ?"); params.append(vault_id)
```

In `api/routes/catalog.py`, add `vault_id: str | None = None` to the query function signature and pass through.

Commit:
```bash
git add frontend/catalog.html api/routes/catalog.py core/manager.py
git commit -m "feat(layer4): Vault Log page — vault filter on catalog"
```

---

### Task 4.5: Update `frontend/search.html` → vault selector

**Files:**
- Modify: `frontend/search.html`

Add vault selector checkboxes above all three tabs:

```html
<div id="vault-selector" class="mb-4">
  <span class="text-sm font-medium text-gray-600 mr-2">Vaults:</span>
  <!-- populated by JS from GET /api/vaults -->
</div>
```

JS to populate:
```javascript
async function loadVaultSelector() {
  const vaults = await api('/api/vaults');
  const container = document.getElementById('vault-selector');
  vaults.forEach(v => {
    const label = document.createElement('label');
    label.className = 'inline-flex items-center mr-3 text-sm';
    label.innerHTML = `
      <input type="checkbox" class="vault-cb mr-1" value="${v.vault_id}" checked>
      <span style="color:${v.color}">${v.name}</span>`;
    container.appendChild(label);
  });
}
```

Pass selected `vault_ids` as comma-separated query param to search and query endpoints. Update `api/routes/search.py` and `api/routes/query.py` to accept and filter by `vault_ids`.

Commit:
```bash
git add frontend/search.html api/routes/search.py api/routes/query.py
git commit -m "feat(layer4): vault selector checkboxes on search page"
```

---

### Task 4.6: Update `frontend/settings.html` → multi-level tabs

**Files:**
- Modify: `frontend/settings.html`

Replace the current flat tab layout with two levels:

```
Settings
├── Global    ← existing settings (Ollama, Qdrant, Tesseract, monitor thresholds)
└── Vaults
    ├── [+ New Vault]
    └── [vault name tab per vault]
        ├── General (name, directory, priority, colour)
        ├── LLM
        ├── Extraction
        ├── Embeddings
        └── Danger Zone
```

Implementation approach:
1. On load, fetch `GET /api/vaults` to build vault tabs dynamically
2. Each vault tab loads `GET /api/vaults/{vault_id}/settings` for current values
3. Save calls `POST /api/vaults/{vault_id}/settings?key=...` per changed field
4. Danger Zone shows Archive button; after archive shows Gut; after gut shows Delete (with name-confirm)
5. Add monitor threshold settings to Global tab (new section, reads/writes via existing settings API)

Add these settings to `core/settings.py` schema:
```python
'monitor:enabled':           {'type': 'string', 'default': 'true',  'label': 'Enable governor', 'group': 'monitor'},
'monitor:sample_interval':   {'type': 'int',    'default': 60,       'label': 'Sample interval (s)', 'group': 'monitor'},
'monitor:gpu_temp_throttle': {'type': 'float',  'default': 80.0,     'label': 'GPU temp throttle (°C)', 'group': 'monitor'},
'monitor:gpu_temp_cooldown': {'type': 'float',  'default': 88.0,     'label': 'GPU temp cooldown (°C)', 'group': 'monitor'},
'monitor:gpu_util_throttle': {'type': 'float',  'default': 70.0,     'label': 'GPU util throttle (%)', 'group': 'monitor'},
'monitor:sustained_minutes': {'type': 'int',    'default': 3,        'label': 'Sustained minutes', 'group': 'monitor'},
'monitor:cooldown_minutes':  {'type': 'int',    'default': 10,       'label': 'Cooldown minutes', 'group': 'monitor'},
'monitor:ram_throttle_pct':  {'type': 'float',  'default': 85.0,     'label': 'RAM throttle (%)', 'group': 'monitor'},
'monitor:cpu_temp_throttle': {'type': 'float',  'default': 85.0,     'label': 'CPU temp throttle (°C)', 'group': 'monitor'},
```

Commit:
```bash
git add frontend/settings.html core/settings.py
git commit -m "feat(layer4): multi-level settings tabs — Global + per-vault; monitor thresholds"
```

---

### Task 4.7: Add logs.db clear to `frontend/utils.html`

**Files:**
- Modify: `frontend/utils.html`
- Modify: `api/routes/utils.py`

Add a "Diagnostics" card to the Utilities page with a "Clear Logs Database" button.

In `api/routes/utils.py`:
```python
@router.post("/utils/clear_logs")
def clear_logs():
    from core.manager import get_logs_db_path, _connect
    with _connect(get_logs_db_path()) as conn:
        conn.executescript("""
            DELETE FROM task_timings;
            DELETE FROM worker_errors;
            DELETE FROM worker_log;
            DELETE FROM extractor_stats;
            DELETE FROM system_stats;
        """)
        conn.commit()
    return {"ok": True}
```

In the UI, use the same two-step confirmation pattern as the existing Rebuild Index button.

Commit:
```bash
git add frontend/utils.html api/routes/utils.py
git commit -m "feat(layer4): add Clear Logs Database button to Utilities page"
```

---

### Task 4.8: Final integration validation and memory update

**Step 1: Run all offline rigs**

```bash
python tests/rigs/check_db_migration.py --verbose
python tests/rigs/check_settings_resolution.py --verbose
python tests/rigs/check_resource_governor.py --verbose
python tests/rigs/check_worker_priority.py --verbose
```
All must exit 0.

**Step 2: Run full pytest suite**

```bash
venv\Scripts\pytest tests/unit/ -v
```
All must pass.

**Step 3: Start server and run Layer 3 rig**

```bash
venv\Scripts\python run.py
python tests/rigs/check_vault_api.py --verbose
```

**Step 4: Manual UI walkthrough**

- [ ] Navigate to http://localhost:8000 — resource strip shows, Documents vault card visible
- [ ] Navigate to /catalog — vault filter dropdown present, files visible under Documents
- [ ] Navigate to /search — vault selector checkboxes present, search returns results
- [ ] Navigate to /settings — Global tab works, Vaults tab shows Documents vault
- [ ] Create a second vault in Settings → Vaults → [+ New Vault]
- [ ] Verify second vault appears in vault selector on search page
- [ ] Archive the test vault; confirm it disappears from active search
- [ ] Restore the test vault
- [ ] Navigate to /utils — Clear Logs button visible

**Step 5: Update project status doc**

Create `docs/status/2026-03-03-project-status.md` documenting:
- All four layers complete
- New files added
- Settings schema additions
- Known limitations / next steps

**Step 6: Final commit**

```bash
git add docs/status/2026-03-03-project-status.md
git commit -m "docs: update project status — infrastructure migration complete"
```

**Migration complete.**

---

## Summary of All New/Modified Files

| File | Action | Layer |
|---|---|---|
| `requirements.txt` | Modified | Setup |
| `tests/rigs/check_db_migration.py` | Created | 1 |
| `tests/rigs/check_settings_resolution.py` | Created | 2 |
| `tests/rigs/check_resource_governor.py` | Created | 2 |
| `tests/rigs/check_worker_priority.py` | Created | 2 |
| `tests/rigs/check_vault_api.py` | Created | 3 |
| `tests/rigs/README.md` | Created | 4 |
| `tests/unit/test_logs_db.py` | Created | 1 |
| `tests/unit/test_vaults_schema.py` | Created | 1 |
| `tests/unit/test_vault_bootstrap.py` | Created | 1 |
| `tests/unit/test_extractor_contracts.py` | Created | 2 |
| `tests/unit/test_settings_resolver.py` | Created | 2 |
| `tests/unit/test_worker_priority.py` | Created | 2 |
| `tests/unit/test_monitor.py` | Created | 2 |
| `tests/unit/test_vault_manager.py` | Created | 3 |
| `core/extractors/__init__.py` | Created | 2 |
| `core/extractors/base.py` | Created | 2 |
| `core/monitor.py` | Created | 2 |
| `core/vault_manager.py` | Created | 3 |
| `core/manager.py` | Modified | 1, 2, 3 |
| `core/settings.py` | Modified | 2, 4 |
| `core/ingestor.py` | Modified | 3 |
| `workers/utils.py` | Modified | 2 |
| `workers/extraction_worker.py` | Modified | 2 |
| `workers/embedding_worker.py` | Modified | 2 |
| `run.py` | Modified | 1, 2 |
| `api/routes/vaults.py` | Created | 3 |
| `api/routes/monitor.py` | Created | 3 |
| `api/routes/catalog.py` | Modified | 4 |
| `api/routes/search.py` | Modified | 4 |
| `api/routes/query.py` | Modified | 4 |
| `api/routes/utils.py` | Modified | 4 |
| `api/main.py` | Modified | 3, 4 |
| `frontend/index.html` | Modified | 4 |
| `frontend/catalog.html` | Modified | 4 |
| `frontend/search.html` | Modified | 4 |
| `frontend/settings.html` | Modified | 4 |
| `frontend/utils.html` | Modified | 4 |
