# Pipeline Stall Sensors — Design Spec

## Goal

Detect embedding worker stalls (not just extraction stalls) and visualize pipeline queue depth + stall state on the telemetry page's HW tab.

## Background

The existing `_check_stall()` in `core/monitor.py` only watches extraction activity (PENDING count + `task_timings` MAX completed_at). When the embedding worker stalls — e.g. Qdrant down, Python bug, worker crash — the EXTRACTED queue backs up silently with no visible indicator. This feature adds a parallel embedding stall sensor and surfaces both sensors alongside a queue depth chart on the telemetry page.

---

## Architecture

### 1. Embedding Worker — activate `_record_timing()`

**File:** `workers/embedding_worker.py`

`_record_timing()` is already defined but never called. After each successful batch in `process_task_batch()`, call it once per completed document (those that had chunks — `doc_batches` entries only; `empty_hashes` documents produce no meaningful elapsed time and are excluded).

Compute elapsed as: record `batch_start = time.time()` before the `embedder.embed_batch()` call; after all upserts succeed, compute `total_elapsed = time.time() - batch_start`; then `elapsed_per_doc = total_elapsed / len(doc_batches)`.

```python
batch_start = time.time()
# ... embed_batch + upsert loop ...
elapsed_per_doc = (time.time() - batch_start) / max(len(doc_batches), 1)
for fh in doc_batches:
    _record_timing(task_by_hash[fh], 'embedding', elapsed_per_doc)
```

`task_by_hash` must be added as a new line at the start of `process_task_batch()`: `task_by_hash = {t['file_hash']: t for t in tasks}`. This dict does not currently exist in the function.

The `task_timings` table already has: `file_hash, vault_id, extractor, file_size, elapsed_secs, completed_at`.

---

### 2. Monitor — embedding stall detection

**File:** `core/monitor.py`

#### 2a. Module-level state (parallel to extraction stall)

```python
_embed_stall_detected: bool = False
_embed_stall_minutes: int = 0
_embed_stall_lock = threading.Lock()

def get_embed_stall_state() -> tuple[bool, int]:
    with _embed_stall_lock:
        return _embed_stall_detected, _embed_stall_minutes

def _set_embed_stall_state(detected: bool, minutes: int):
    global _embed_stall_detected, _embed_stall_minutes
    with _embed_stall_lock:
        _embed_stall_detected = detected
        _embed_stall_minutes = minutes
```

#### 2b. `_check_embed_stall()` method on `HardwareMonitor`

Logic:
1. Count tasks with `status='EXTRACTED'` in `docvault.db` using a separate `_connect(get_db_path())` call (not the logs.db connection — see cross-DB note in section 2c)
2. If count == 0: clear stall state, return (nothing waiting = no stall)
3. Query `logs.db task_timings` for `MAX(completed_at) WHERE extractor='embedding'`
4. If no embedding completions ever (`last_completion IS NULL`): match the existing `_check_stall()` behavior — clear stall state and return without firing. This avoids spurious alerts on service restart (where EXTRACTED tasks exist but embedding hasn't had a chance to run yet). The stall will trigger naturally once a baseline exists and the worker subsequently stops.
5. Compute `stall_mins = (now - last_completion).total_seconds() / 60`
6. `stalled = stall_mins >= threshold_mins`
7. Fire alert once on transition to stalled (same pattern as `_check_stall()`)

Threshold setting: `monitor:embed_stall_threshold_mins` (default: 5)

Alert text: `f"{extracted_count:,} tasks waiting in EXTRACTED queue, no embedding activity for {stall_mins}m"`

Wrapped in try/except; never raises (same pattern as `_check_stall()`).

#### 2c. Queue depth sampling — cross-DB approach

`system_stats` lives in `logs.db`; tasks live in `docvault.db`. Do NOT use `ATTACH DATABASE` — WAL mode across two files on Windows can cause locking issues. Instead, open a second connection to `docvault.db` to read queue counts, then close it before writing to `logs.db`:

```python
def _record_sample(reading: MonitorReading, state: str):
    try:
        # Read queue counts from docvault.db first (separate connection)
        extracted_q, embedding_q = 0, 0
        try:
            from core.manager import _connect, get_db_path
            with _connect(get_db_path()) as task_conn:
                extracted_q = task_conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='EXTRACTED'"
                ).fetchone()[0]
                embedding_q = task_conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='EMBEDDING'"
                ).fetchone()[0]
        except Exception:
            pass  # queue counts are best-effort

        # Write to logs.db
        with _connect(get_logs_db_path()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO system_stats
                   (sampled_at, ..., extracted_queue, embedding_queue)
                   VALUES (?, ..., ?, ?)""",
                (..., extracted_q, embedding_q)
            )
            conn.commit()
    except Exception:
        pass
```

#### 2d. Schema migration

Add to `core/manager.py` `init_logs_db()`:

```python
# Idempotent — ignore if columns already exist
for col, coltype in [('extracted_queue', 'INTEGER'), ('embedding_queue', 'INTEGER')]:
    try:
        conn.execute(f'ALTER TABLE system_stats ADD COLUMN {col} {coltype}')
    except Exception:
        pass  # column already exists
```

`init_logs_db()` is called at startup before the monitor thread starts (confirmed: `run.py` calls DB init before launching workers), so the columns are guaranteed to exist when `_record_sample()` first runs.

#### 2e. Main loop

Call `_check_embed_stall()` in the same cycle as `_check_stall()`:

```python
self._check_stall()
self._check_embed_stall()
```

---

### 3. Settings Schema

**File:** `core/settings.py`

Add to the `monitor` group:

```python
'monitor:embed_stall_threshold_mins': {
    'default': '5',
    'type': 'int',
    'label': 'Embedding stall threshold (minutes)',
    'description': 'Minutes without embedding activity before a stall is declared (requires non-empty EXTRACTED queue).',
    'group': 'monitor',
},
```

---

### 4. API

**File:** `api/routes/monitor.py`

#### `/api/monitor/status` — add embed stall fields

The existing extraction stall (`stalled`, `stall_minutes`) comes from `get_stall_state()` in `core/monitor.py` and is currently exposed on `/api/workers/status`. The embedding stall is added to `/api/monitor/status`. The telemetry page already fetches both endpoints; `embed_stall` fields go to `/monitor/status`, extraction stall stays on `/workers/status`.

Also add `embed_stall_threshold_mins` to the existing `thresholds` dict in the `/monitor/status` response so the frontend can compute the amber state dynamically (rather than hardcoding a default):

The existing `thresholds` field is a `ThrottleThresholds` dataclass instance serialized by FastAPI. To add the new key, convert it to a dict first:

```python
import dataclasses
from core.monitor import get_embed_stall_state
from core.settings import settings

embed_stalled, embed_stall_mins = get_embed_stall_state()
embed_threshold = int(settings.get('monitor:embed_stall_threshold_mins') or 5)

thresholds_dict = dataclasses.asdict(_load_thresholds())
thresholds_dict['embed_stall_threshold_mins'] = embed_threshold

# In response dict:
{
    ...existing fields...,
    "embed_stall": embed_stalled,
    "embed_stall_minutes": embed_stall_mins,
    "thresholds": thresholds_dict,
}
```

#### `/api/monitor/history` — add queue depth to existing array-of-dicts format

The history endpoint returns a list of row dicts (`[dict(r) for r in rows]`). The two new columns are naturally included once added to `system_stats` — no restructuring needed. Each dict in the list gains `extracted_queue` and `embedding_queue` keys. The frontend accesses them as `d.extracted_queue`.

No response format change required; the frontend slices the last 30 samples client-side for the chart (does not add a `?limit=` parameter).

---

### 5. Frontend — Telemetry Page

**File:** `frontend/telemetry.html`

#### 5a. New "Pipeline Health" section

Add below the HW sensor cards on the HW tab. Layout:

```
[ PIPELINE HEALTH ]
[ stall banner — only visible when either stall active ]
[ queue depth chart (full width)                       ]
[ extraction stall card ] [ embedding stall card       ]
```

#### 5b. Stall alert banner

Appears when `/workers/status` `stalled` OR `/monitor/status` `embed_stall` is true. Shows which pipeline is stalled and for how long. Auto-hides when both clear.

```html
<div id="ph-banner" class="ph-banner" style="display:none">
  <span class="blink">◉</span>
  <span id="ph-banner-text"></span>
</div>
```

#### 5c. Queue depth chart

30-sample window: slice `history.slice(-30)` client-side from the `/api/monitor/history` response (the full history may be up to 60+ samples; the chart shows the most recent 30). CSS bar chart (no canvas dependency). Each sample = one bar group: EXTRACTED (blue `#1d4ed8`) + EMBEDDING (purple `#7c3aed`). Y-axis auto-scales to `Math.max(...allValues)`.

Updated on each `/api/monitor/history` poll (existing 10s interval on HW tab).

#### 5d. Stall sensor cards

Two cards matching the existing HW sensor card style (`border-left` color coding). Data sources:

- **Extraction stall**: reads `workers_status.stalled`, `workers_status.stall_minutes` (from `/api/workers/status`, already polled)
- **Embedding stall**: reads `monitor_status.embed_stall`, `monitor_status.embed_stall_minutes`, `monitor_status.thresholds.embed_stall_threshold_mins` (from `/api/monitor/status`)

States:
- Green: not stalled, or queue empty
- Amber: `embed_stall_minutes > threshold * 0.5 AND extracted_queue > 0 AND NOT embed_stall`
- Red: `embed_stall == true`

---

## Data Flow

```
embedding_worker.process_task_batch()
  └─► _record_timing(extractor='embedding') → logs.db task_timings

HardwareMonitor._run() [every N secs]
  ├─► _check_stall()        → extraction stall state (unchanged)
  ├─► _check_embed_stall()  → embedding stall state (new)
  └─► _record_sample()      → logs.db system_stats (+ extracted_queue, embedding_queue)
        └─► reads task counts from docvault.db (separate connection, closed before logs.db write)

GET /api/workers/status   → stalled, stall_minutes (unchanged)
GET /api/monitor/status   → + embed_stall, embed_stall_minutes, thresholds.embed_stall_threshold_mins
GET /api/monitor/history  → + extracted_queue, embedding_queue per dict in array

telemetry.html HW tab [polls every 10s]
  ├─► fetch /api/workers/status  → extraction stall card
  ├─► fetch /api/monitor/status  → embedding stall card + banner
  └─► fetch /api/monitor/history → queue depth chart (slice last 30)
```

---

## Error Handling

- `_check_embed_stall()`: wrapped in try/except, never raises
- Queue count queries in `_record_sample()`: inner try/except; on failure stores 0 (not NULL, so chart renders cleanly)
- Frontend: if `extracted_queue`/`embedding_queue` missing from history dicts (old server pre-migration), default to 0 — `d.extracted_queue ?? 0`
- Fresh install with no embedding history: stall detection is suppressed (matching `_check_stall()` behavior). Once the first batch completes, a baseline is established and subsequent stalls are detected normally.

---

## Testing

**File:** `tests/test_pipeline_stall_sensors.py`

- `_check_embed_stall()` with empty EXTRACTED queue → no stall
- `_check_embed_stall()` with queue + recent completion (< threshold) → no stall
- `_check_embed_stall()` with queue + stale completion (>= threshold) → stall fires
- `_check_embed_stall()` with queue + no completions ever → stall fires (first-run behavior)
- `_check_embed_stall()` clears when EXTRACTED queue drains to 0
- `_record_sample()` stores extracted_queue + embedding_queue counts
- `GET /api/monitor/status` includes `embed_stall`, `embed_stall_minutes`, `thresholds.embed_stall_threshold_mins`
- `GET /api/monitor/history` response dicts include `extracted_queue`, `embedding_queue` keys
- `process_task_batch()` calls `_record_timing()` with `extractor='embedding'` after successful batch

---

## Files Changed

| File | Change |
|------|--------|
| `workers/embedding_worker.py` | Record batch start time; call `_record_timing()` per completed doc after batch |
| `core/monitor.py` | Embed stall state + getters, `_check_embed_stall()`, queue counts in `_record_sample()` |
| `core/settings.py` | Add `monitor:embed_stall_threshold_mins` |
| `core/manager.py` | `init_logs_db()` migration: idempotent ALTER TABLE for two new columns |
| `api/routes/monitor.py` | Add `embed_stall`, `embed_stall_minutes`, `thresholds.embed_stall_threshold_mins` to status; history gets new columns automatically |
| `frontend/telemetry.html` | Pipeline Health section: banner + queue chart + two stall cards |
| `tests/test_pipeline_stall_sensors.py` | New test file (9 tests) |
