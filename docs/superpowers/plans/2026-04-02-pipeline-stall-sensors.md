# Pipeline Stall Sensors Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add embedding stall detection to the monitor and visualize both extraction and embedding stall sensors + queue depth chart on the telemetry page's HW tab.

**Architecture:** Activate `_record_timing()` in the embedding worker so `task_timings` records embedding completions. Add `_check_embed_stall()` to `HardwareMonitor` mirroring the existing `_check_stall()` pattern. Sample EXTRACTED/EMBEDDING queue counts into `system_stats` each monitor cycle. Expose new fields in `/api/monitor/status` and surface everything in a new "Pipeline Health" section on `telemetry.html`.

**Tech Stack:** Python (SQLite, threading), FastAPI, vanilla JS, CSS bar chart (no canvas for the queue depth chart).

---

## File Map

| File | What changes |
|------|-------------|
| `workers/embedding_worker.py` | Add `task_by_hash`, `batch_start` timer, call `_record_timing()` after batch |
| `core/manager.py` | `init_logs_db()`: two new columns on `system_stats` via idempotent ALTER TABLE |
| `core/monitor.py` | New embed stall module-state + getters, `_check_embed_stall()`, queue counts in `_record_sample()` |
| `core/settings.py` | Add `monitor:embed_stall_threshold_mins` |
| `api/routes/monitor.py` | `/api/monitor/status`: add embed_stall fields + convert thresholds to dict |
| `frontend/telemetry.html` | Pipeline Health section: banner + queue chart + two stall cards |
| `tests/test_pipeline_stall_sensors.py` | New — 9 tests covering all stall logic paths |

---

## Chunk 1: Backend — Data Layer

### Task 1: DB migration — add queue columns to system_stats

**Files:**
- Modify: `core/manager.py:112-119` (existing migration block in `init_logs_db()`)
- Test: `tests/test_pipeline_stall_sensors.py` (create file)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_stall_sensors.py
import sqlite3, tempfile, os, pytest

def make_logs_db():
    """Create an in-memory logs.db and run init_logs_db() against it."""
    import unittest.mock as mock
    tmp = tempfile.mktemp(suffix='.db')
    with mock.patch('core.manager.get_logs_db_path', return_value=tmp):
        from core.manager import init_logs_db
        init_logs_db()
    return tmp

def test_system_stats_has_queue_columns():
    db = make_logs_db()
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(system_stats)').fetchall()]
    conn.close()
    os.unlink(db)
    assert 'extracted_queue' in cols
    assert 'embedding_queue' in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_system_stats_has_queue_columns -v
```
Expected: FAIL — columns not yet present.

- [ ] **Step 3: Add migration to `init_logs_db()`**

In `core/manager.py`, after the existing `disk_free_pct` migration block (lines 117-118) and **before** the `conn.commit()` at line 120, add:

```python
        if 'extracted_queue' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN extracted_queue INTEGER")
        if 'embedding_queue' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN embedding_queue INTEGER")
```

The final lines 113-120 should read:
```python
        cursor = conn.execute("PRAGMA table_info(system_stats)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'disk_free_gb' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN disk_free_gb REAL")
        if 'disk_free_pct' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN disk_free_pct REAL")
        if 'extracted_queue' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN extracted_queue INTEGER")
        if 'embedding_queue' not in columns:
            conn.execute("ALTER TABLE system_stats ADD COLUMN embedding_queue INTEGER")

        conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_system_stats_has_queue_columns -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/manager.py tests/test_pipeline_stall_sensors.py
git commit -m "feat(monitor): add extracted_queue/embedding_queue columns to system_stats"
```

---

### Task 2: Embedding worker — record timing after each batch

**Files:**
- Modify: `workers/embedding_worker.py:129-196` (`process_task_batch`)
- Test: `tests/test_pipeline_stall_sensors.py`

The `_record_timing()` function at line 199 of `embedding_worker.py` already writes to `task_timings`. It is never called. We add the call.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_pipeline_stall_sensors.py
from unittest.mock import patch, MagicMock

def test_process_task_batch_records_timing():
    """After a successful batch, _record_timing is called once per doc with extractor='embedding'."""
    import workers.embedding_worker as ew

    task = {
        'file_hash': 'abc123',
        'file_path': '/fake/doc.txt',
        'extracted_text': 'hello world ' * 100,
        'vault_id': None,
        'file_size': 100,
    }

    mock_vs = MagicMock()
    mock_vs.upsert_batch.return_value = None

    recorded = []

    def fake_record_timing(t, extractor, elapsed):
        recorded.append((t['file_hash'], extractor, elapsed))

    with patch('workers.embedding_worker.embedder.embed_batch', return_value=[[0.1]*384]):
        with patch('workers.embedding_worker.manager.update_task_status'):
            with patch('workers.embedding_worker.manager.update_task_progress'):
                with patch('workers.embedding_worker._record_timing', side_effect=fake_record_timing):
                    ew.process_task_batch('/fake/db', [task], mock_vs)

    assert len(recorded) == 1
    assert recorded[0][0] == 'abc123'
    assert recorded[0][1] == 'embedding'
    assert recorded[0][2] > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_process_task_batch_records_timing -v
```
Expected: FAIL — `_record_timing` never called.

- [ ] **Step 3: Modify `process_task_batch()` in `workers/embedding_worker.py`**

Add `task_by_hash` at the top of the function and `batch_start` before the embed call. Add the timing loop after marking tasks COMPLETED.

At the very start of `process_task_batch` (line 130, after the docstring), add:
```python
    task_by_hash = {t['file_hash']: t for t in tasks}
```

Before the `# Phase 2` comment (line 158), add:
```python
    batch_start = time.time()
```

After `logger.info(f"  Batch complete: {len(doc_batches)} doc(s).")` (line 196), add:
```python
    elapsed_per_doc = (time.time() - batch_start) / max(len(doc_batches), 1)
    for fh in doc_batches:
        _record_timing(task_by_hash[fh], 'embedding', elapsed_per_doc)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_process_task_batch_records_timing -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workers/embedding_worker.py tests/test_pipeline_stall_sensors.py
git commit -m "feat(embed): record timing after each batch for stall detection"
```

---

### Task 3: monitor.py — embed stall state + `_check_embed_stall()`

**Files:**
- Modify: `core/monitor.py:316-333` (stall state block) and `core/monitor.py:566-580` (run loop)
- Test: `tests/test_pipeline_stall_sensors.py`

- [ ] **Step 1: Write the failing tests (3 tests)**

```python
# Add to tests/test_pipeline_stall_sensors.py
import threading, time
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

def _make_monitor():
    """Return a HardwareMonitor instance without starting its loop."""
    from core.monitor import HardwareMonitor
    with patch('core.monitor.PSUtilSensor') as m1, \
         patch('core.monitor.NvidiaSensor') as m2, \
         patch('core.monitor.WMICPUTempSensor') as m3, \
         patch('core.monitor.DiskSensor') as m4:
        for m in (m1, m2, m3, m4):
            inst = m.return_value
            inst.initialise.return_value = False
        mon = HardwareMonitor()
    return mon

def test_embed_stall_no_extracted_queue():
    """No EXTRACTED tasks → no stall."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    # create=True because _connect is imported locally inside _check_embed_stall
    with patch('core.manager._connect') as mock_conn:
        mock_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'n': 0}
        monitor._check_embed_stall()

    stalled, mins = mon_mod.get_embed_stall_state()
    assert stalled is False
    assert mins == 0

def test_embed_stall_recent_completion():
    """EXTRACTED tasks exist + recent embedding completion → no stall."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    call_count = [0]
    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            # docvault.db: EXTRACTED count
            row.__getitem__ = lambda s, k: 5
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        else:
            # logs.db: last embedding completion
            row.__getitem__ = lambda s, k: recent
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    # Patch _connect with create=True (locally imported); patch the real settings object's get method
    with patch('core.manager._connect', side_effect=fake_connect), \
         patch.object(__import__('core.settings', fromlist=['settings']).settings, 'get', return_value='5'):
        monitor._check_embed_stall()

    stalled, _ = mon_mod.get_embed_stall_state()
    assert stalled is False

def test_embed_stall_stale_completion():
    """EXTRACTED tasks exist + stale embedding completion → stall fires."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    call_count = [0]
    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            row.__getitem__ = lambda s, k: 10
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        else:
            row.__getitem__ = lambda s, k: stale
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    with patch('core.manager._connect', side_effect=fake_connect), \
         patch.object(__import__('core.settings', fromlist=['settings']).settings, 'get', return_value='5'), \
         patch('core.alerts.send_alert', create=True):
        monitor._check_embed_stall()

    stalled, mins = mon_mod.get_embed_stall_state()
    assert stalled is True
    assert mins >= 5

def test_embed_stall_no_history():
    """No embedding history ever → stall suppressed (matches _check_stall behavior)."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(False, 0)
    monitor = _make_monitor()

    call_count = [0]
    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1:
            row.__getitem__ = lambda s, k: 5  # EXTRACTED count
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        else:
            row.__getitem__ = lambda s, k: None  # no completions
            ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    with patch('core.manager._connect', side_effect=fake_connect), \
         patch.object(__import__('core.settings', fromlist=['settings']).settings, 'get', return_value='5'):
        monitor._check_embed_stall()

    stalled, _ = mon_mod.get_embed_stall_state()
    assert stalled is False

def test_embed_stall_clears_when_queue_empty():
    """Stall clears when EXTRACTED queue drains to zero."""
    from core import monitor as mon_mod
    mon_mod._set_embed_stall_state(True, 10)
    monitor = _make_monitor()

    def fake_connect(path):
        ctx = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda s, k: 0  # empty queue
        ctx.__enter__.return_value.execute.return_value.fetchone.return_value = row
        return ctx

    with patch('core.manager._connect', side_effect=fake_connect):
        monitor._check_embed_stall()

    stalled, _ = mon_mod.get_embed_stall_state()
    assert stalled is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py -k "embed_stall" -v
```
Expected: errors — `_set_embed_stall_state`, `get_embed_stall_state`, `_check_embed_stall` not defined.

- [ ] **Step 3: Add embed stall state to `core/monitor.py`**

After the existing `_set_stall_state` block (after line 332), add:

```python
# Shared embedding stall state (set by HardwareMonitor, read by API)
_embed_stall_detected: bool = False
_embed_stall_minutes: int = 0
_embed_stall_lock = threading.Lock()


def get_embed_stall_state() -> tuple[bool, int]:
    """Returns (stalled: bool, stall_minutes: int) for embedding pipeline."""
    with _embed_stall_lock:
        return _embed_stall_detected, _embed_stall_minutes


def _set_embed_stall_state(detected: bool, minutes: int):
    global _embed_stall_detected, _embed_stall_minutes
    with _embed_stall_lock:
        _embed_stall_detected = detected
        _embed_stall_minutes = minutes
```

- [ ] **Step 4: Add `_check_embed_stall()` to `HardwareMonitor`**

Add this method to the `HardwareMonitor` class, directly after `_check_stall()` (after line 564):

```python
    def _check_embed_stall(self):
        """
        Detect an embedding stall: EXTRACTED tasks exist but no embedding activity
        for longer than monitor:embed_stall_threshold_mins.
        Mirrors _check_stall() pattern. Best-effort; never raises.
        """
        try:
            from core.settings import settings as s
            threshold_mins = int(s.get('monitor:embed_stall_threshold_mins') or 5)

            from core.manager import get_logs_db_path, get_db_path, _connect
            from datetime import datetime, timezone

            # Step 1: count EXTRACTED tasks (separate connection — docvault.db)
            with _connect(get_db_path()) as dconn:
                extracted = dconn.execute(
                    "SELECT COUNT(*) as n FROM tasks WHERE status='EXTRACTED'"
                ).fetchone()['n']

            # Step 2: nothing waiting — no stall possible
            if extracted == 0:
                _set_embed_stall_state(False, 0)
                return

            # Step 3: last embedding completion from logs.db
            with _connect(get_logs_db_path()) as lconn:
                row = lconn.execute(
                    "SELECT MAX(completed_at) as last FROM task_timings WHERE extractor='embedding'"
                ).fetchone()
                last_str = row['last'] if row else None

            # Step 4: no history — suppress stall (matches _check_stall() behaviour)
            # On first run the worker hasn't established a baseline yet.
            if not last_str:
                _set_embed_stall_state(False, 0)
                return

            last_dt = datetime.fromisoformat(last_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            stall_mins = int((datetime.now(timezone.utc) - last_dt).total_seconds() // 60)
            stalled = stall_mins >= threshold_mins

            was_stalled, _ = get_embed_stall_state()
            _set_embed_stall_state(stalled, stall_mins)

            if stalled and not was_stalled:
                logger.error(
                    f"Embedding stall: {extracted:,} tasks in EXTRACTED queue, "
                    f"no embedding activity for {stall_mins}m",
                    ext="monitor"
                )
                try:
                    from core.alerts import send_alert
                    send_alert(
                        title="DocVault embedding stalled",
                        message=f"{extracted:,} tasks waiting in EXTRACTED queue but no embedding activity for {stall_mins} minutes.",
                        level='error',
                        source='monitor',
                    )
                except Exception:
                    pass
            elif not stalled and was_stalled:
                logger.info("Embedding stall cleared — embedding activity resumed", ext="monitor")

        except Exception:
            pass
```

- [ ] **Step 5: Call `_check_embed_stall()` in the run loop**

In `HardwareMonitor.run()`, after `self._check_stall()` (line 577), add:

```python
                    self._check_embed_stall()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py -k "embed_stall" -v
```
Expected: all 5 pass.

- [ ] **Step 7: Commit**

```bash
git add core/monitor.py tests/test_pipeline_stall_sensors.py
git commit -m "feat(monitor): add embedding stall detection"
```

---

### Task 4: monitor.py — queue counts in `_record_sample()`

**Files:**
- Modify: `core/monitor.py:443-462` (`_record_sample`)
- Test: `tests/test_pipeline_stall_sensors.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_pipeline_stall_sensors.py
import sqlite3, tempfile

def test_record_sample_stores_queue_counts():
    """_record_sample writes extracted_queue and embedding_queue to system_stats."""
    import unittest.mock as mock
    from core.monitor import MonitorReading, _record_sample

    reading = MonitorReading(
        sampled_at='2026-01-01T00:00:00',
        cpu_pct=10, cpu_temp=40,
        ram_used_gb=2, ram_total_gb=16, ram_pct=12,
        gpu_temp=0, gpu_util_pct=0,
        vram_used_gb=0, vram_total_gb=0,
        disk_free_gb=100, disk_free_pct=50,
    )

    tmp_logs = tempfile.mktemp(suffix='.db')
    tmp_tasks = tempfile.mktemp(suffix='.db')

    # Bootstrap system_stats table (with new columns)
    conn = sqlite3.connect(tmp_logs)
    conn.execute("""CREATE TABLE system_stats (
        sampled_at TEXT PRIMARY KEY, cpu_pct REAL, cpu_temp REAL,
        ram_used_gb REAL, ram_total_gb REAL, ram_pct REAL,
        gpu_temp REAL, gpu_util_pct REAL, vram_used_gb REAL, vram_total_gb REAL,
        disk_free_gb REAL, disk_free_pct REAL, throttle_state TEXT,
        extracted_queue INTEGER, embedding_queue INTEGER
    )""")
    conn.commit(); conn.close()

    # Bootstrap tasks table
    conn2 = sqlite3.connect(tmp_tasks)
    conn2.execute("CREATE TABLE tasks (file_hash TEXT, status TEXT)")
    conn2.execute("INSERT INTO tasks VALUES ('h1','EXTRACTED')")
    conn2.execute("INSERT INTO tasks VALUES ('h2','EXTRACTED')")
    conn2.execute("INSERT INTO tasks VALUES ('h3','EMBEDDING')")
    conn2.commit(); conn2.close()

    with mock.patch('core.manager.get_logs_db_path', return_value=tmp_logs), \
         mock.patch('core.manager.get_db_path', return_value=tmp_tasks):
        _record_sample(reading, 'normal')

    conn = sqlite3.connect(tmp_logs)
    row = conn.execute("SELECT extracted_queue, embedding_queue FROM system_stats").fetchone()
    conn.close()

    import os; os.unlink(tmp_logs); os.unlink(tmp_tasks)
    assert row[0] == 2   # extracted_queue
    assert row[1] == 1   # embedding_queue
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_record_sample_stores_queue_counts -v
```
Expected: FAIL.

- [ ] **Step 3: Update `_record_sample()` in `core/monitor.py`**

Replace the existing `_record_sample` function (lines 443-462) with:

```python
def _record_sample(reading: MonitorReading, state: str):
    """Write sample to logs.db system_stats. Best-effort."""
    try:
        from core.manager import get_logs_db_path, get_db_path, _connect

        # Read queue counts from docvault.db (separate connection — different DB file)
        extracted_q, embedding_q = 0, 0
        try:
            with _connect(get_db_path()) as task_conn:
                extracted_q = task_conn.execute(
                    "SELECT COUNT(*) as n FROM tasks WHERE status='EXTRACTED'"
                ).fetchone()['n']
                embedding_q = task_conn.execute(
                    "SELECT COUNT(*) as n FROM tasks WHERE status='EMBEDDING'"
                ).fetchone()['n']
        except Exception:
            pass  # queue counts are best-effort

        with _connect(get_logs_db_path()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO system_stats
                   (sampled_at, cpu_pct, cpu_temp, ram_used_gb, ram_total_gb, ram_pct,
                    gpu_temp, gpu_util_pct, vram_used_gb, vram_total_gb,
                    disk_free_gb, disk_free_pct, throttle_state,
                    extracted_queue, embedding_queue)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (reading.sampled_at, reading.cpu_pct, reading.cpu_temp,
                 reading.ram_used_gb, reading.ram_total_gb, reading.ram_pct,
                 reading.gpu_temp, reading.gpu_util_pct,
                 reading.vram_used_gb, reading.vram_total_gb,
                 reading.disk_free_gb, reading.disk_free_pct, state,
                 extracted_q, embedding_q)
            )
            conn.commit()
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_record_sample_stores_queue_counts -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/monitor.py tests/test_pipeline_stall_sensors.py
git commit -m "feat(monitor): sample EXTRACTED/EMBEDDING queue counts into system_stats"
```

---

### Task 5: Settings — add `monitor:embed_stall_threshold_mins`

**Files:**
- Modify: `core/settings.py:308-311` (after existing `monitor:stall_threshold_mins` entry)

No test needed — this is a schema declaration; correctness is verified by the API test in Task 6.

- [ ] **Step 1: Add the setting**

In `core/settings.py`, after the `monitor:stall_threshold_mins` entry (after line 311), add:

```python
            'monitor:embed_stall_threshold_mins': {
                'type': 'int', 'default': 5, 'label': 'Embed stall threshold (m)', 'group': 'monitor',
                'description': 'Minutes without embedding activity (while EXTRACTED tasks exist) before the embedding pipeline is flagged as stalled.',
            },
```

- [ ] **Step 2: Verify setting loads**

```bash
cd E:/DocVault && python -c "
from core.settings import settings
v = settings.get('monitor:embed_stall_threshold_mins')
print('embed_stall_threshold_mins:', v)
assert v is not None
"
```
Expected: `embed_stall_threshold_mins: 5`

- [ ] **Step 3: Commit**

```bash
git add core/settings.py
git commit -m "feat(settings): add monitor:embed_stall_threshold_mins"
```

---

## Chunk 2: API Layer

### Task 6: API — expose embed stall in `/api/monitor/status`

**Files:**
- Modify: `api/routes/monitor.py:1-48`
- Test: `tests/test_pipeline_stall_sensors.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_pipeline_stall_sensors.py
from fastapi.testclient import TestClient

def test_monitor_status_has_embed_stall_fields():
    """GET /api/monitor/status includes embed_stall, embed_stall_minutes, thresholds.embed_stall_threshold_mins."""
    from unittest.mock import patch, MagicMock
    from api.main import app

    client = TestClient(app)

    with patch('api.routes.monitor.get_embed_stall_state', return_value=(True, 12)):
        resp = client.get('/api/monitor/status')

    assert resp.status_code == 200
    data = resp.json()
    assert 'embed_stall' in data
    assert 'embed_stall_minutes' in data
    assert data['embed_stall'] is True
    assert data['embed_stall_minutes'] == 12
    assert 'thresholds' in data
    assert 'embed_stall_threshold_mins' in data['thresholds']
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_monitor_status_has_embed_stall_fields -v
```
Expected: FAIL — fields not present.

- [ ] **Step 3: Update `api/routes/monitor.py`**

Replace the full file with:

```python
import dataclasses
from fastapi import APIRouter, Query
from core.monitor import get_throttle_state, get_last_reading, _load_thresholds, get_embed_stall_state
from core import manager
from core.settings import settings

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/status")
def monitor_status():
    reading = get_last_reading()
    state, reason = get_throttle_state()
    embed_stalled, embed_stall_mins = get_embed_stall_state()
    embed_threshold = int(settings.get('monitor:embed_stall_threshold_mins') or 5)
    active_tasks = manager.get_active_tasks(manager.get_db_path())

    thresholds_dict = dataclasses.asdict(_load_thresholds())
    thresholds_dict['embed_stall_threshold_mins'] = embed_threshold

    if reading is None:
        return {
            "state": state,
            "reason": reason,
            "available": False,
            "active_tasks": active_tasks,
            "thresholds": thresholds_dict,
            "embed_stall": embed_stalled,
            "embed_stall_minutes": embed_stall_mins,
        }

    return {
        "state":               state,
        "reason":              reason,
        "available":           True,
        "cpu_pct":             reading.cpu_pct,
        "cpu_temp":            reading.cpu_temp,
        "ram_used_gb":         reading.ram_used_gb,
        "ram_total_gb":        reading.ram_total_gb,
        "ram_pct":             reading.ram_pct,
        "gpu_temp":            reading.gpu_temp,
        "gpu_util_pct":        reading.gpu_util_pct,
        "vram_used_gb":        reading.vram_used_gb,
        "vram_total_gb":       reading.vram_total_gb,
        "disk_free_gb":        reading.disk_free_gb,
        "disk_free_pct":       reading.disk_free_pct,
        "sampled_at":          reading.sampled_at,
        "active_tasks":        active_tasks,
        "thresholds":          thresholds_dict,
        "embed_stall":         embed_stalled,
        "embed_stall_minutes": embed_stall_mins,
    }


@router.get("/history")
def monitor_history(hours: int = Query(1, ge=1, le=24)):
    """Returns historical system statistics from logs.db."""
    return manager.get_system_stats_history(hours)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_monitor_status_has_embed_stall_fields -v
```
Expected: PASS.

- [ ] **Step 5: Verify history response includes queue keys**

The `/api/monitor/history` endpoint uses `SELECT *` from `system_stats` — after the migration in Task 1, the new columns appear automatically. Write a quick smoke test:

```python
# Add to tests/test_pipeline_stall_sensors.py
def test_monitor_history_includes_queue_keys():
    """GET /api/monitor/history dicts include extracted_queue and embedding_queue."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from api.main import app

    client = TestClient(app)

    fake_history = [
        {'sampled_at': '2026-01-01T00:00:00', 'cpu_pct': 10, 'extracted_queue': 5, 'embedding_queue': 1},
    ]
    with patch('core.manager.get_system_stats_history', return_value=fake_history):
        resp = client.get('/api/monitor/history?hours=1')

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert 'extracted_queue' in rows[0]
    assert 'embedding_queue' in rows[0]
```

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py::test_monitor_history_includes_queue_keys -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/routes/monitor.py tests/test_pipeline_stall_sensors.py
git commit -m "feat(api): expose embed_stall fields and queue keys in monitor endpoints"
```

---

## Chunk 3: Frontend

### Task 7: telemetry.html — Pipeline Health section

**Files:**
- Modify: `frontend/telemetry.html` — HTML section + CSS + JS updates

The HW tab currently ends at line 574 (`</div><!-- end tab-hardware -->`). We insert the Pipeline Health section before that closing tag.

The `update()` function at line 648 already fetches `/monitor/status` and `/monitor/history`. The `renderStatus()` and `renderHistory()` functions already run on each poll. We extend those two functions plus add a new `renderPipelineHealth()`.

- [ ] **Step 1: Add CSS for pipeline health section**

In `frontend/telemetry.html`, find the existing `<style>` block. After the `.sensor-grid` styles, add:

```css
        /* Pipeline Health */
        .pipeline-section {
            margin: 1.5rem 0;
        }
        .pipeline-section-header {
            font-size: 0.65rem;
            letter-spacing: 0.15em;
            color: var(--emerald);
            text-transform: uppercase;
            border-bottom: 1px solid rgba(51,238,119,0.1);
            padding-bottom: 0.3rem;
            margin-bottom: 0.75rem;
        }
        .ph-banner {
            background: rgba(255,68,68,0.08);
            border: 1px solid var(--ruby);
            border-radius: 3px;
            padding: 0.5rem 0.75rem;
            color: var(--ruby);
            font-size: 0.75rem;
            margin-bottom: 0.75rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .ph-banner .blink { animation: blink 1s step-end infinite; }
        @keyframes blink { 50% { opacity: 0; } }
        .ph-queue-chart {
            background: var(--terminal-gray);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 3px;
            padding: 0.75rem;
            margin-bottom: 0.75rem;
        }
        .ph-chart-label {
            font-size: 0.6rem;
            letter-spacing: 0.1em;
            color: rgba(148,163,184,0.5);
            text-transform: uppercase;
            margin-bottom: 0.5rem;
        }
        .ph-bars {
            height: 60px;
            display: flex;
            align-items: flex-end;
            gap: 2px;
        }
        .ph-bar-group {
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            gap: 1px;
        }
        .ph-bar {
            border-radius: 1px 1px 0 0;
            min-height: 1px;
            transition: height 0.3s;
        }
        .ph-bar.extracted { background: #1d4ed8; }
        .ph-bar.embedding { background: #7c3aed; }
        .ph-legend {
            display: flex;
            gap: 1rem;
            margin-top: 0.4rem;
        }
        .ph-legend-item {
            display: flex;
            align-items: center;
            gap: 0.3rem;
            font-size: 0.6rem;
            color: rgba(148,163,184,0.6);
        }
        .ph-legend-dot {
            width: 8px; height: 8px;
            border-radius: 1px;
        }
        .ph-legend-dot.extracted { background: #1d4ed8; }
        .ph-legend-dot.embedding { background: #7c3aed; }
        .ph-stall-cards {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.75rem;
        }
        .ph-stall-card {
            background: var(--terminal-gray);
            border: 1px solid rgba(255,255,255,0.06);
            border-left: 3px solid var(--emerald);
            border-radius: 3px;
            padding: 0.75rem;
            transition: border-left-color 0.3s;
        }
        .ph-stall-card.warn { border-left-color: var(--amber); }
        .ph-stall-card.stalled { border-left-color: var(--ruby); }
        .ph-stall-label {
            font-size: 0.6rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: rgba(148,163,184,0.5);
            margin-bottom: 0.3rem;
        }
        .ph-stall-value {
            font-size: 1.4rem;
            color: var(--emerald);
            font-weight: bold;
            line-height: 1;
        }
        .ph-stall-value.warn { color: var(--amber); }
        .ph-stall-value.stalled { color: var(--ruby); }
        .ph-stall-sub {
            font-size: 0.65rem;
            color: rgba(148,163,184,0.4);
            margin-top: 0.2rem;
        }
        .ph-stall-badge {
            display: inline-block;
            margin-top: 0.4rem;
            padding: 0.1rem 0.4rem;
            border-radius: 2px;
            font-size: 0.6rem;
            letter-spacing: 0.08em;
        }
        .ph-stall-badge.ok { background: rgba(51,238,119,0.1); color: var(--emerald); }
        .ph-stall-badge.warn { background: rgba(245,158,11,0.1); color: var(--amber); }
        .ph-stall-badge.stalled { background: rgba(255,68,68,0.1); color: var(--ruby); }
```

- [ ] **Step 2: Add HTML section before `</div><!-- end tab-hardware -->`**

Find the line `</div><!-- end tab-hardware -->` (line 574) and insert before it:

```html
        <!-- Pipeline Health section -->
        <div class="pipeline-section">
            <div class="pipeline-section-header">Pipeline Health</div>

            <!-- Stall alert banner (hidden by default) -->
            <div id="ph-banner" class="ph-banner" style="display:none">
                <span class="blink">◉</span>
                <span id="ph-banner-text"></span>
            </div>

            <!-- Queue depth chart -->
            <div class="ph-queue-chart">
                <div class="ph-chart-label">Queue Depth — last 30 samples</div>
                <div class="ph-bars" id="ph-bars">
                    <div style="font-size:0.7rem;opacity:0.3;align-self:center">No data yet.</div>
                </div>
                <div class="ph-legend">
                    <div class="ph-legend-item"><div class="ph-legend-dot extracted"></div>EXTRACTED</div>
                    <div class="ph-legend-item"><div class="ph-legend-dot embedding"></div>EMBEDDING</div>
                </div>
            </div>

            <!-- Stall sensor cards -->
            <div class="ph-stall-cards">
                <div class="ph-stall-card" id="ph-extract-card">
                    <div class="ph-stall-label">Extraction Pipeline</div>
                    <div class="ph-stall-value" id="ph-extract-val">—</div>
                    <div class="ph-stall-sub" id="ph-extract-sub">waiting for data</div>
                    <span class="ph-stall-badge ok" id="ph-extract-badge">UNKNOWN</span>
                </div>
                <div class="ph-stall-card" id="ph-embed-card">
                    <div class="ph-stall-label">Embedding Pipeline</div>
                    <div class="ph-stall-value" id="ph-embed-val">—</div>
                    <div class="ph-stall-sub" id="ph-embed-sub">waiting for data</div>
                    <span class="ph-stall-badge ok" id="ph-embed-badge">UNKNOWN</span>
                </div>
            </div>
        </div>
```

- [ ] **Step 3: Add `renderPipelineHealth()` JS function**

Find the `renderHistory` function (line 882) and add the new function immediately before it:

```javascript
        function renderPipelineHealth(status, history, workersStatus) {
            // --- Banner ---
            const extractStalled = workersStatus && workersStatus.stalled;
            const embedStalled   = status && status.embed_stall;
            const banner = document.getElementById('ph-banner');
            const bannerText = document.getElementById('ph-banner-text');

            if (extractStalled || embedStalled) {
                const parts = [];
                if (extractStalled) parts.push(`Extraction stalled ${workersStatus.stall_minutes}m`);
                if (embedStalled)   parts.push(`Embedding stalled ${status.embed_stall_minutes}m`);
                bannerText.textContent = parts.join(' · ');
                banner.style.display = 'flex';
            } else {
                banner.style.display = 'none';
            }

            // --- Queue depth chart ---
            const window30 = (history || []).slice(-30);
            const barsEl = document.getElementById('ph-bars');
            if (window30.length === 0) {
                barsEl.innerHTML = '<div style="font-size:0.7rem;opacity:0.3;align-self:center">No data yet.</div>';
            } else {
                const allVals = window30.flatMap(d => [d.extracted_queue ?? 0, d.embedding_queue ?? 0]);
                const maxVal = Math.max(...allVals, 1);
                barsEl.innerHTML = window30.map(d => {
                    const eq = d.extracted_queue ?? 0;
                    const emq = d.embedding_queue ?? 0;
                    const eqH  = Math.max(1, Math.round((eq  / maxVal) * 56));
                    const emqH = Math.max(1, Math.round((emq / maxVal) * 56));
                    return `<div class="ph-bar-group">
                        <div class="ph-bar embedding" style="height:${emqH}px" title="Embedding: ${emq}"></div>
                        <div class="ph-bar extracted" style="height:${eqH}px" title="Extracted: ${eq}"></div>
                    </div>`;
                }).join('');
            }

            // --- Extraction stall card ---
            const extractCard  = document.getElementById('ph-extract-card');
            const extractVal   = document.getElementById('ph-extract-val');
            const extractSub   = document.getElementById('ph-extract-sub');
            const extractBadge = document.getElementById('ph-extract-badge');

            if (workersStatus) {
                if (extractStalled) {
                    extractCard.className  = 'ph-stall-card stalled';
                    extractVal.className   = 'ph-stall-value stalled';
                    extractVal.textContent = `${workersStatus.stall_minutes}m`;
                    extractSub.textContent = 'since last extraction';
                    extractBadge.className = 'ph-stall-badge stalled';
                    extractBadge.textContent = '◉ STALLED';
                } else {
                    extractCard.className  = 'ph-stall-card';
                    extractVal.className   = 'ph-stall-value';
                    extractVal.textContent = `${workersStatus.stall_minutes || 0}m`;
                    extractSub.textContent = 'since last extraction';
                    extractBadge.className = 'ph-stall-badge ok';
                    extractBadge.textContent = 'ACTIVE';
                }
            }

            // --- Embedding stall card ---
            const embedCard  = document.getElementById('ph-embed-card');
            const embedVal   = document.getElementById('ph-embed-val');
            const embedSub   = document.getElementById('ph-embed-sub');
            const embedBadge = document.getElementById('ph-embed-badge');

            if (status) {
                const embedThreshold = (status.thresholds && status.thresholds.embed_stall_threshold_mins) || 5;
                const embedMins  = status.embed_stall_minutes || 0;
                const isAmber    = !embedStalled && embedMins > embedThreshold * 0.5;

                if (embedStalled) {
                    embedCard.className  = 'ph-stall-card stalled';
                    embedVal.className   = 'ph-stall-value stalled';
                    embedBadge.className = 'ph-stall-badge stalled';
                    embedBadge.textContent = '◉ STALLED';
                } else if (isAmber) {
                    embedCard.className  = 'ph-stall-card warn';
                    embedVal.className   = 'ph-stall-value warn';
                    embedBadge.className = 'ph-stall-badge warn';
                    embedBadge.textContent = 'SLOWING';
                } else {
                    embedCard.className  = 'ph-stall-card';
                    embedVal.className   = 'ph-stall-value';
                    embedBadge.className = 'ph-stall-badge ok';
                    embedBadge.textContent = 'ACTIVE';
                }
                embedVal.textContent = `${embedMins}m`;
                embedSub.textContent = 'since last embedding';
            }
        }
```

- [ ] **Step 4: Wire `renderPipelineHealth()` into the `update()` function**

The `update()` function (line 648) already fetches `/monitor/status` and `/monitor/history`. It does NOT currently fetch `/workers/status`. Add it to the `Promise.all` call and wire the render.

Find:
```javascript
                const [status, history, queue, health] = await Promise.all([
                    api('/monitor/status'),
                    api(`/monitor/history?hours=${historyHours}`),
                    api('/catalog?status=PENDING&limit=10'),
                    api('/utils/health')
                ]);
```

Replace with:
```javascript
                const [status, history, queue, health, workersStatus] = await Promise.all([
                    api('/monitor/status'),
                    api(`/monitor/history?hours=${historyHours}`),
                    api('/catalog?status=PENDING&limit=10'),
                    api('/utils/health'),
                    api('/workers/status'),
                ]);
```

Then after `renderHealth(health);`, add:
```javascript
                renderPipelineHealth(status, history, workersStatus);
```

- [ ] **Step 5: Restart server and verify visually**

```bash
cd E:/DocVault && python run.py
```

Open `http://localhost:8000/telemetry` → HW tab. Verify:
- "Pipeline Health" section appears below the sensor cards
- Queue chart renders (bars appear after next monitor cycle, ~60s)
- Extraction and embedding stall cards show current state
- No JS console errors

- [ ] **Step 6: Commit**

```bash
git add frontend/telemetry.html
git commit -m "feat(telemetry): add Pipeline Health section with queue chart and stall cards"
```

---

## Chunk 3 Review Gate

Run the full test suite before wrapping up:

```bash
cd E:/DocVault && python -m pytest tests/test_pipeline_stall_sensors.py -v
```

Expected: all 9 tests pass.

```bash
cd E:/DocVault && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: no regressions.

---
