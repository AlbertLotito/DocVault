# DocVault Performance Tuning System — Design Spec
**Date:** 2026-03-14
**Status:** Approved

---

## Goal

Build a three-component performance tuning system that lets users measure, monitor, and automatically optimise DocVault's extraction and embedding throughput. The output is a CLI benchmark tool, a live analytics dashboard, and a Mission Control optimizer triggered from the Utilities page.

---

## Audiences

**End users (archivists, power users):** They interact primarily with the Mission Control optimizer and the analytics dashboard — they should not need to understand the underlying parameters.

**Developers / advanced users:** They run the standalone CLI benchmark to profile specific configurations or file types.

---

## Component 1 — Standalone Benchmark Script (`tools/benchmark.py`)

### Purpose

A CLI tool that measures current extraction throughput by running a controlled sample of real vault files through the extraction pipeline in-process. Results are printed to the terminal and persisted to the `benchmark_runs` table in `logs.db`.

### Behaviour

- Selects **COMPLETED tasks only** from `docvault.db` (verified extractable content; PENDING/ERROR tasks would skew timings)
- Samples up to N files per type category. When more than N candidates exist, selects randomly without replacement using `random.sample()` — no fixed seed, results vary per run
- Categories: `text` (txt, md, py, js…), `pdf`, `image` (jpg, png, bmp…), `audio` (mp3, opus, wav…), `video` (mp4, avi…)
- Runs each file through its extractor chain using `core/router.py`'s `get_extractors(file_type)` to resolve the extractor list. Each extractor is invoked via a `LegacyExtractorAdapter` (wrapping legacy extractors) or directly if already a `BaseExtractor`. A synthetic `ExtractorContext` is constructed (same fields as `extraction_worker._build_context()` — `vault_id`, `file_hash`, `cancel_token=threading.Event()`, `logger`, `settings=SettingsResolver(vault_id)`). The adapter's `.run(file_path, ctx)` method is called and the result discarded — **only `result.elapsed_secs` is recorded**. No results are written to `docvault.db`. No task status is mutated.
- Throughput figures are **in-process throughput** (excludes queue/DB overhead). This is labelled explicitly in all output.
- Saves one summary row to `benchmark_runs` unless `--no-save` is passed. Runs with `--no-save` do not appear in the Analytics dashboard.

### CLI flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--samples N` | 10 | Files per type to sample |
| `--types text,pdf,...` | all | Restrict to specific type categories |
| `--vault VAULT_ID \| all` | `all` | Sample from a specific vault or all active vaults. SQL: omit `WHERE vault_id=?` when `all` |
| `--no-save` | off | Skip writing to logs.db |

### Output format

```
DocVault Benchmark — 2026-03-14 14:22:01
Settings: chunk_size=600  max_parallel=1  embed_model=nomic-embed-text
Note: throughput is in-process (excludes queue/DB overhead)

File type    Samples  Avg time   P95      Throughput (in-process)
-----------  -------  ---------  -------  -----------------------
text              10     0.08s    0.12s    45,000 f/hr
pdf               10     1.40s    2.10s     2,571 f/hr
image             10     4.20s    6.80s       857 f/hr  ← BOTTLENECK
audio              8    18.30s   24.00s       196 f/hr
video              3    42.10s   55.00s        85 f/hr

Overall (weighted by queue depth): 847 f/hr (in-process)
Queue drain estimate: ~21.6 hours at current speed

Bottleneck: vision extractor (4.2s avg) — GPU-bound
Tip: Disabling vision for non-art vaults would raise throughput ~3×
Results saved to logs.db (run_id: 7)
```

---

## Component 2 — Analytics Dashboard (Telemetry page, "Performance" tab)

### Purpose

A new "Performance" tab in `frontend/telemetry.html`. Reads from `logs.db` (`task_timings` and `benchmark_runs`). No new data collection from workers.

### Layout

**Section 1 — Extractor Performance**
Horizontal bars per extractor. Sorted slowest-first. Color-coded: green (< 1s avg), amber (1–10s), red (> 10s). Columns: extractor name, avg elapsed, p95, file count.

**Section 2 — Throughput Trend**
Sparkline: files/hour over last 24h or 7d (tab toggle). Computed by bucketing `task_timings.completed_at` into hourly bins.

**Section 3 — Bottleneck Callout**
Plain-English card: *"Your slowest extractor is vision (4.2s avg, 1,234 files). Disabling it for non-art vaults would increase throughput by ~3×."*

**Section 4 — Benchmark Run History**
Table of last 5 `benchmark_runs`: run date, overall throughput (in-process), bottleneck extractor. No Compare feature.

### Backend: `GET /api/utils/performance_stats`

```json
{
  "extractor_stats": [
    {"extractor": "vision", "avg_secs": 4.2, "p95_secs": 6.8, "count": 1234, "category": "slow"}
  ],
  "throughput_24h": [{"hour": "14:00", "files_per_hour": 847}],
  "throughput_7d":  [{"day": "2026-03-13", "files_per_hour": 920}],
  "bottleneck":     {"extractor": "vision", "avg_secs": 4.2, "tip": "..."},
  "benchmark_runs": [
    {"run_id": 7, "run_at": "2026-03-14T14:22:01", "overall_files_per_hour": 847, "bottleneck_extractor": "vision"}
  ]
}
```

### New `logs.db` table

Added in `manager.init_logs_db()`:

```sql
CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at                 TEXT NOT NULL,
    results                TEXT NOT NULL,            -- JSON: per-type stats
    bottleneck_extractor   TEXT,
    overall_files_per_hour REAL
);
```

No `settings_snapshot` column — not needed anywhere (YAGNI).

---

## Component 3 — Mission Control Optimizer

### Trigger

**"⚡ Run Optimizer"** button in a new "Performance Tuning" section in `frontend/utils.html`. Opens a full-screen Mission Control overlay `<div>`. No new route or nav item.

### Threading Model

Daemon thread (`threading.Thread(daemon=True)`) stored as module-level `_optimizer_thread` in `core/tuner.py`. Abort via `threading.Event` (`_abort_event`). Status via module-level `_state: dict` protected by `threading.Lock`. FastAPI endpoints read/write `_state` for polling.

### Concurrency Guard

`POST /api/utils/optimizer/start` returns **HTTP 409** `{"error": "optimizer already running"}` if `_optimizer_thread is not None and _optimizer_thread.is_alive()`.

`POST /api/utils/optimizer/abort` returns **HTTP 400** `{"error": "no active run"}` if state is `idle`, `complete`, or `aborted`.

### Settings Snapshot and Crash Recovery

**Order of operations at sweep start:**
1. Read all current production values of the swept keys from `settings.db` first (do not rely on settings.get() after writing the snapshot, to avoid accidentally reading the snapshot back)
2. Write the snapshot to `settings.db` as a raw key `tuning:_optimizer_snapshot` (JSON string), using a **direct DB write** (`manager._connect(get_settings_db_path())` + `INSERT OR REPLACE INTO settings`) — not via `settings.set()` which requires schema membership

**Reading the snapshot** (in rollback/abort/startup): also via direct DB query `SELECT value FROM settings WHERE key='tuning:_optimizer_snapshot'`, not via `settings.get()` (which would return `None` for schema-less keys).

**Startup rollback in `run.py`**: after `init_settings_db()`, before `init_db(DB_PATH)`, check for `tuning:_optimizer_snapshot` via direct DB query. If found: parse the JSON, iterate key-value pairs, call `settings.set(key, value)` for each (schema-validated keys only — invalid keys silently dropped), then delete the snapshot key via direct DB write, then log a warning. This is a **settings-only rollback** — task state and Qdrant vectors are untouched.

**On clean completion:** delete `tuning:_optimizer_snapshot` from settings.db, resume workers.
**On abort:** set `_abort_event`, thread detects it, restores snapshot, deletes key, resumes workers.

### Workers During Run

`manager.set_pause_state(True)` pauses extraction and embedding workers. **Ingestor is not paused** — new PENDING tasks may accumulate; this is acceptable.

### Sweep Matrix

Fixed grid (no user-configurable run count — the grid is deterministic):
- `embeddings:chunk_size`: [400, 600, 800, 1000]
- `embeddings:chunk_overlap`: [50, 100]
- `ollama:max_parallel`: [1, 2] — the headroom check runs once at the start of the optimizer thread's sweep function (`_run_sweep()`), reads live GPU utilisation from `HardwareMonitor`'s latest reading (`get_latest_reading().gpu_util_pct`). If `gpu_util_pct < 80` (i.e. > 20% headroom), includes 2. If no GPU sensor available (`gpu_util_pct is None`), uses [1] only.
- `monitor:gpu_temp_throttle`: [current_setting, min(current_setting + 5, 90)] — `current_setting` = value read from snapshot. The `90` ceiling is intentional as the hardware-safe maximum.

Total combinations: 8–16 depending on hardware.

### Baseline

**Baseline is the first sweep run**, using the production settings snapshot values. It is not a separate pre-sweep measurement. The first run's throughput becomes `baseline_throughput` in all subsequent poll responses.

### Profile Selection

After all runs complete:

- **Raw Speed**: run with highest throughput, any thermals
- **Sustainable**: highest-throughput run where `max_gpu_temp_during_run ≤ current_setting` (original throttle from snapshot). If no GPU sensor data is available (CPU-only machine), Sustainable = Raw Speed (degenerate)
- **Balanced**: highest score = `0.6 × (throughput / max_throughput) + 0.4 × ((90 − max_gpu_temp) / 90)`. If no GPU sensor, Balanced score = throughput only (thermal term dropped)

All three profile keys are **always present** in the `profiles` object, even in degenerate cases where they point to identical parameter sets. The `apply` endpoint always accepts all three names.

If all three resolve to the same run: display a single "Recommended" card.
If Raw Speed == Sustainable: display two cards (Sustainable + Balanced).
Otherwise: display all three.

### API Endpoints (6 total)

All registered in `api/routes/utils.py` **before** any existing parameterised routes:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/utils/performance_stats` | Analytics data |
| `POST` | `/api/utils/optimizer/start` | Launch (409 if running) |
| `GET` | `/api/utils/optimizer/status` | Live poll at 1000ms |
| `POST` | `/api/utils/optimizer/apply` | Apply profile (400 if not complete) |
| `POST` | `/api/utils/optimizer/abort` | Cancel (400 if not running) |

That is 5 optimizer endpoints + 1 analytics = **6 new endpoints total**.

### Status Poll Response

Polled at **1000ms** by the frontend:

```json
{
  "state": "idle | starting | running | complete | aborted | error",
  "run_index": 3,
  "total_runs": 8,
  "current_params": {"chunk_size": 800, "chunk_overlap": 100, "max_parallel": 2, "gpu_temp_throttle": 85},
  "current_throughput": 1247,
  "baseline_throughput": 847,
  "runs": [
    {"index": 1, "params": {...}, "throughput": 847, "max_gpu_temp": 71},
    {"index": 2, "params": {...}, "throughput": 1012, "max_gpu_temp": 73}
  ],
  "hardware": {"gpu_temp": 72, "gpu_util": 64, "ram_pct": 38, "cpu_temp": 61},
  "profiles": null
}
```

When `state == "complete"`, `profiles` is populated. All three keys always present:
```json
{
  "profiles": {
    "raw_speed":   {"params": {...}, "throughput": 1247, "label": "Raw Speed"},
    "sustainable": {"params": {...}, "throughput": 1012, "label": "Sustainable"},
    "balanced":    {"params": {...}, "throughput": 1100, "label": "Balanced"}
  }
}
```

### `POST /api/utils/optimizer/apply`

Body: `{"profile": "raw_speed" | "sustainable" | "balanced"}`. Returns HTTP 400 if `state != "complete"`. Writes chosen profile's params to `settings.db` via `settings.set()`. Returns `{"applied": true, "params": {...}}`.

### Mission Control UI — Quadrant Command Layout

Full-screen dark overlay (`background: #050809`) as a fixed-position `<div>` in `frontend/utils.html`. Toggled by `hidden` class.

**Top-left — Hardware Gauges**
Four gauge blocks stacked vertically: GPU Temp, GPU Util, RAM %, CPU Temp. Each: large monospace value + color-coded fill bar (green/amber/red) + label. Live-updated from status poll. If a sensor is unavailable, displays `—` (no error).

**Top-right — Throughput**
Large monospace files/hour in green. Below: delta vs baseline (`↑ +18% vs baseline`). Below: sparkline — one bar per completed run, bar height proportional to throughput, grows left to right as runs complete.

**Mid-right — Parameter Display**
During run: four read-only rows showing current test param name, value, and delta from snapshot (e.g. `chunk_size  800  ↑ from 600`).
After run completes: replaced by 1–3 profile cards (see Profile Selection). Each card: profile name, throughput, key params, "Apply This Profile" button.

**Bottom-right — Event Log**
Monospace log feed. Newest at bottom. Scrollable. Color-coded: blue = info, green = improvement found, amber = hardware warning, red = abort/error.

**Bottom bar (full width)**
Left: `RUN 3 / 8 · ETA ~12 min` — ETA computed by linear extrapolation: `(elapsed_time / runs_completed) × runs_remaining`. Approximate.
Right: `■ Abort` (red, always active during run) and `↓ Apply Best Profile` (indigo, disabled until `state == complete`; applies the Raw Speed profile by default unless a card is clicked).

---

## Settings Schema Addition

One new key in `core/settings.py` schema (the `optimizer_runs` key is dropped — YAGNI, grid is deterministic):

```python
'tuning:benchmark_samples_per_type': {
    'type': 'int', 'default': 10,
    'label': 'Benchmark samples per type', 'group': 'lab',
    'description': 'Number of COMPLETED files per type sampled during benchmark and optimizer mini-benchmarks. Random selection, no fixed seed.',
},
```

`tuning:_optimizer_snapshot` is a reserved internal key written/read directly via DB — **not in schema**, not user-configurable, not surfaced in Settings UI.

---

## File Map

| File | Change |
|------|--------|
| `tools/benchmark.py` | **New** — standalone CLI benchmark |
| `core/tuner.py` | **New** — optimizer thread, sweep, mini-benchmark, profile selection, `_state` dict |
| `api/routes/utils.py` | **Modify** — 6 new endpoints before parameterised routes |
| `core/manager.py` | **Modify** — `benchmark_runs` table in `init_logs_db()` |
| `core/settings.py` | **Modify** — 1 new `tuning:` key in schema |
| `run.py` | **Modify** — startup snapshot rollback (after `init_settings_db()`, before `bootstrap_default_vault()`) |
| `frontend/telemetry.html` | **Modify** — add "Performance" tab |
| `frontend/utils.html` | **Modify** — add Performance Tuning section + Mission Control overlay `<div>` |

---

## Constraints

- Optimizer always restores original settings on abort, error, or crash (snapshot persisted to `settings.db` before sweep; deleted on clean finish)
- Workers (extraction + embedding) paused during run; **ingestor not paused**
- Benchmark is read-only on the task queue — no task status mutated
- All extractor timing already collected — no new worker instrumentation
- Mission Control is a `div` overlay — no new route or nav item
- GPU throttle relaxation capped at 90°C
- One optimizer run at a time (409 guard)
- Benchmark `--no-save` runs do not appear in the Analytics dashboard (expected behaviour)
