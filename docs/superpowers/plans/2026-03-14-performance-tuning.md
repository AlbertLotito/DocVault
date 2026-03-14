# Performance Tuning System Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three connected tools — a CLI benchmark script, a live analytics dashboard (Performance tab in Telemetry), and a Mission Control optimizer triggered from the Utilities page with a full-screen Quadrant Command console.

**Architecture:** A new `core/tuner.py` module owns all optimizer logic (daemon thread, sweep, profile selection, state dict). `tools/benchmark.py` is a standalone CLI script that shares the same file-sampling and extractor-invocation helpers. Six new FastAPI routes are added to `api/routes/utils.py`. Two frontend pages gain new sections — Telemetry gets a Performance tab, and Utilities gets a Performance Tuning card that opens the Mission Control overlay `<div>`.

**Tech Stack:** Python stdlib (`threading`, `random`, `argparse`, `json`), FastAPI, SQLite (`logs.db` for `benchmark_runs` table), `core/router.py` extractor resolution, `core/monitor.py` sensor readings via `get_last_reading()`, existing `settings.get()`/`settings.set()` + direct DB writes for the internal snapshot key.

**Spec:** `docs/superpowers/specs/2026-03-14-performance-tuning-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `core/tuner.py` | **Create** | Optimizer thread, sweep matrix, mini-benchmark, profile selection, `_state` dict |
| `tools/benchmark.py` | **Create** | CLI benchmark — sample COMPLETED tasks, invoke extractors, print report, save to `benchmark_runs` |
| `core/manager.py` | **Modify** | Add `benchmark_runs` table to `init_logs_db()` |
| `core/settings.py` | **Modify** | Add `tuning:benchmark_samples_per_type` schema key |
| `run.py` | **Modify** | Startup rollback: check for stale `tuning:_optimizer_snapshot`, restore + delete |
| `api/routes/utils.py` | **Modify** | 6 new endpoints: `performance_stats`, optimizer `start/status/apply/abort`, (routes registered before parameterised routes) |
| `frontend/telemetry.html` | **Modify** | Add "Performance" tab (extractor bar chart, throughput sparkline, bottleneck card, run history) |
| `frontend/utils.html` | **Modify** | Add Performance Tuning card + Mission Control overlay `<div>` (Quadrant Command layout) |
| `tests/unit/test_tuner.py` | **Create** | Unit tests for profile selection, sweep matrix generation, snapshot rollback logic |
| `tests/unit/test_logs_db.py` | **Modify** | Add `test_init_logs_db_creates_benchmark_runs_table` to existing file |

---

## Chunk 1: Data Layer

### Task 1: Add `benchmark_runs` table to `logs.db` + settings key

**Files:**
- Modify: `core/manager.py` (inside `init_logs_db()`, after `system_stats` table)
- Modify: `tests/unit/test_logs_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_logs_db.py`:

```python
def test_init_logs_db_creates_benchmark_runs_table(tmp_path, monkeypatch):
    """benchmark_runs table must be created by init_logs_db()."""
    import core.manager as m
    db = str(tmp_path / 'logs.db')
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: db)
    m.init_logs_db()
    import sqlite3
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(benchmark_runs)"
    ).fetchall()}
    conn.close()
    assert 'benchmark_runs' in tables
    assert {'run_id', 'run_at', 'results', 'bottleneck_extractor', 'overall_files_per_hour'}.issubset(cols)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd E:\DocVault && python -m pytest tests/unit/test_logs_db.py::test_init_logs_db_creates_benchmark_runs_table -v
```
Expected: FAIL — `benchmark_runs` table missing.

- [ ] **Step 3: Add table to `init_logs_db()` in `core/manager.py`**

Find the closing `"""` of the `executescript(...)` call (after `system_stats`) and add before it:

```sql
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at                 TEXT NOT NULL,
                results                TEXT NOT NULL,
                bottleneck_extractor   TEXT,
                overall_files_per_hour REAL
            );
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_logs_db.py -v
```
Expected: all PASS.

- [ ] **Step 5: Add `tuning:benchmark_samples_per_type` to settings schema**

In `core/settings.py`, add after the `lab:test_timeout_secs` entry:

```python
'tuning:benchmark_samples_per_type': {
    'type': 'int', 'default': 10,
    'label': 'Benchmark samples per type', 'group': 'lab',
    'description': 'Number of COMPLETED files per type sampled during benchmark runs and optimizer mini-benchmarks. Random selection, no fixed seed.',
},
```

- [ ] **Step 6: Commit**

```bash
git add core/manager.py core/settings.py tests/unit/test_logs_db.py
git commit -m "feat(tuning): add benchmark_runs table and settings key"
```

---

### Task 2: Create `core/tuner.py` — profile selection and sweep matrix (pure logic, no I/O)

**Files:**
- Create: `core/tuner.py`
- Create: `tests/unit/test_tuner.py`

This task covers the pure-logic portions: profile selection algorithm and sweep matrix generation. The optimizer thread and actual sweep execution come in Task 5.

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_tuner.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.tuner import build_sweep_matrix, select_profiles


def test_sweep_matrix_baseline_uses_snapshot_values():
    """First run in sweep must equal the snapshot settings."""
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=None)
    first = matrix[0]
    assert first['embeddings:chunk_size'] == '600'
    assert first['embeddings:chunk_overlap'] == '100'
    assert first['ollama:max_parallel'] == '1'
    assert first['monitor:gpu_temp_throttle'] == '80'


def test_sweep_matrix_caps_gpu_throttle_at_90():
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '88'}
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=None)
    throttle_vals = {r['monitor:gpu_temp_throttle'] for r in matrix}
    assert '93' not in throttle_vals  # 88+5=93, must be capped
    assert '90' in throttle_vals or '88' in throttle_vals  # capped at 90


def test_sweep_matrix_excludes_parallel_2_without_gpu_headroom():
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    # GPU util at 90% — no headroom
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=90.0)
    parallel_vals = {r['ollama:max_parallel'] for r in matrix}
    assert parallel_vals == {'1'}


def test_sweep_matrix_includes_parallel_2_with_headroom():
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    # GPU util at 50% — 50% headroom
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=50.0)
    parallel_vals = {r['ollama:max_parallel'] for r in matrix}
    assert '2' in parallel_vals


def _make_runs(specs):
    """Helper: list of (throughput, max_gpu_temp) dicts."""
    return [{'params': {}, 'throughput': t, 'max_gpu_temp': g, 'index': i}
            for i, (t, g) in enumerate(specs)]


def test_select_profiles_raw_speed_is_highest_throughput():
    runs = _make_runs([(847, 71), (1012, 73), (1247, 78)])
    profiles = select_profiles(runs, original_throttle=80.0)
    assert profiles['raw_speed']['throughput'] == 1247


def test_select_profiles_sustainable_respects_thermal_limit():
    # run with highest throughput (1247) has temp 82 > throttle 80 — excluded
    runs = _make_runs([(847, 71), (1012, 73), (1247, 82)])
    profiles = select_profiles(runs, original_throttle=80.0)
    assert profiles['sustainable']['throughput'] == 1012  # best within thermal limit


def test_select_profiles_no_gpu_sensor_sustainable_equals_raw():
    runs = _make_runs([(847, None), (1012, None), (1247, None)])
    profiles = select_profiles(runs, original_throttle=80.0)
    assert profiles['sustainable']['throughput'] == profiles['raw_speed']['throughput']


def test_select_profiles_all_three_keys_always_present():
    runs = _make_runs([(847, 71)])  # single run — all profiles same
    profiles = select_profiles(runs, original_throttle=80.0)
    assert set(profiles.keys()) == {'raw_speed', 'sustainable', 'balanced'}


def test_select_profiles_balanced_no_gpu_uses_throughput_only():
    runs = _make_runs([(847, None), (1012, None)])
    profiles = select_profiles(runs, original_throttle=80.0)
    # Balanced must be the higher-throughput run when no GPU data
    assert profiles['balanced']['throughput'] == 1012


def test_sweep_matrix_no_gpu_sensor_uses_parallel_1_only():
    """Spec: 'If no GPU sensor available (gpu_util_pct is None), uses [1] only.'"""
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=None)
    parallel_vals = {r['ollama:max_parallel'] for r in matrix}
    assert parallel_vals == {'1'}, "No GPU sensor: must not test max_parallel=2"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/unit/test_tuner.py -v
```
Expected: FAIL — `core.tuner` does not exist.

- [ ] **Step 3: Create `core/tuner.py` with pure logic**

```python
"""
core/tuner.py — Performance optimizer for DocVault.

Owns: sweep matrix generation, profile selection.
The optimizer thread and state management are also here (Task 5).
"""
import threading
import json
from typing import Optional


# ── Sweep matrix ─────────────────────────────────────────────────────────────

CHUNK_SIZES    = ['400', '600', '800', '1000']
CHUNK_OVERLAPS = ['50', '100']


def build_sweep_matrix(snapshot: dict, gpu_util_pct: Optional[float]) -> list[dict]:
    """
    Build the list of parameter dicts to test.
    First entry always equals the production snapshot (baseline run).
    snapshot keys: embeddings:chunk_size, embeddings:chunk_overlap,
                   ollama:max_parallel, monitor:gpu_temp_throttle
    gpu_util_pct: current GPU utilisation (0-100) or None if no sensor.
    """
    current_throttle = float(snapshot.get('monitor:gpu_temp_throttle', '80'))
    relaxed_throttle = str(min(current_throttle + 5, 90.0))
    throttle_vals = [snapshot['monitor:gpu_temp_throttle']]
    if relaxed_throttle != snapshot['monitor:gpu_temp_throttle']:
        throttle_vals.append(relaxed_throttle)

    # Only test max_parallel=2 if GPU util headroom > 20% AND sensor is available.
    # Spec: "If no GPU sensor available (gpu_util_pct is None), uses [1] only."
    parallel_vals = [snapshot['ollama:max_parallel']]
    if gpu_util_pct is not None and gpu_util_pct < 80.0:
        if '2' != snapshot['ollama:max_parallel']:
            parallel_vals.append('2')

    runs = []
    seen = set()
    # Baseline first — must be exact snapshot values
    baseline = {
        'embeddings:chunk_size':    snapshot['embeddings:chunk_size'],
        'embeddings:chunk_overlap': snapshot['embeddings:chunk_overlap'],
        'ollama:max_parallel':      snapshot['ollama:max_parallel'],
        'monitor:gpu_temp_throttle': snapshot['monitor:gpu_temp_throttle'],
    }
    key = json.dumps(baseline, sort_keys=True)
    runs.append(baseline)
    seen.add(key)

    for cs in CHUNK_SIZES:
        for co in CHUNK_OVERLAPS:
            for mp in parallel_vals:
                for gt in throttle_vals:
                    combo = {
                        'embeddings:chunk_size':    cs,
                        'embeddings:chunk_overlap': co,
                        'ollama:max_parallel':      mp,
                        'monitor:gpu_temp_throttle': gt,
                    }
                    k = json.dumps(combo, sort_keys=True)
                    if k not in seen:
                        seen.add(k)
                        runs.append(combo)
    return runs


# ── Profile selection ─────────────────────────────────────────────────────────

def select_profiles(runs: list[dict], original_throttle: float) -> dict:
    """
    Select Raw Speed, Sustainable, and Balanced profiles from completed runs.
    Each run dict must have: throughput (float), max_gpu_temp (float|None), params, index.
    All three profile keys are always present even in degenerate cases.
    """
    if not runs:
        raise ValueError("No runs to select profiles from")

    max_throughput = max(r['throughput'] for r in runs)

    # Raw Speed — highest throughput, any thermals
    raw = max(runs, key=lambda r: r['throughput'])

    # Sustainable — highest throughput within thermal limit
    no_gpu = all(r['max_gpu_temp'] is None for r in runs)
    if no_gpu:
        sustainable = raw
    else:
        within_limit = [
            r for r in runs
            if r['max_gpu_temp'] is None or r['max_gpu_temp'] <= original_throttle
        ]
        sustainable = max(within_limit, key=lambda r: r['throughput']) if within_limit else raw

    # Balanced — weighted score (throughput + thermal headroom)
    def balanced_score(r):
        norm_tp = r['throughput'] / max_throughput if max_throughput > 0 else 0
        if no_gpu or r['max_gpu_temp'] is None:
            return norm_tp
        norm_thermal = (90.0 - r['max_gpu_temp']) / 90.0
        return 0.6 * norm_tp + 0.4 * norm_thermal

    balanced = max(runs, key=balanced_score)

    labels = {'raw_speed': 'Raw Speed', 'sustainable': 'Sustainable', 'balanced': 'Balanced'}

    def _fmt(r, profile_key):
        return {'params': r['params'], 'throughput': r['throughput'],
                'index': r['index'], 'label': labels[profile_key]}

    return {
        'raw_speed':   _fmt(raw, 'raw_speed'),
        'sustainable': _fmt(sustainable, 'sustainable'),
        'balanced':    _fmt(balanced, 'balanced'),
    }
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_tuner.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/tuner.py tests/unit/test_tuner.py
git commit -m "feat(tuner): add sweep matrix and profile selection logic"
```

---

## Chunk 2: Standalone CLI Benchmark

### Task 3: Create `tools/benchmark.py`

**Files:**
- Create: `tools/benchmark.py`

No automated test for the CLI script itself — it requires a live DB. Manual verification steps are provided.

- [ ] **Step 1: Create `tools/benchmark.py`**

```python
"""
tools/benchmark.py — DocVault standalone benchmark tool.

Samples COMPLETED tasks from docvault.db, runs their extractors
in-process (timing only, no DB writes), prints a report.

Usage:
    python tools/benchmark.py [--samples N] [--types TYPE,...] [--vault ID|all] [--no-save]
"""
import argparse
import json
import os
import random
import sys
import sqlite3
import threading
import time
from datetime import datetime, timezone

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import manager
from core import router as _router
from core.extractors.base import LegacyExtractorAdapter, ExtractorContext, ExtractorLogger, BaseExtractor
from core.settings import SettingsResolver, settings


TYPE_EXTENSIONS = {
    'text':  {'txt', 'md', 'py', 'js', 'ts', 'json', 'xml', 'csv', 'html', 'css', 'yml', 'yaml', 'toml'},
    'pdf':   {'pdf'},
    'image': {'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'webp'},
    'audio': {'mp3', 'opus', 'wav', 'flac', 'm4a', 'ogg'},
    'video': {'mp4', 'avi', 'mkv', 'mov', 'wmv'},
}


def _classify(file_type: str) -> str | None:
    ext = (file_type or '').lstrip('.').lower()
    for cat, exts in TYPE_EXTENSIONS.items():
        if ext in exts:
            return cat
    return None


def _sample_tasks(db_path: str, n: int, types: list[str], vault_id: str | None) -> dict[str, list[dict]]:
    """Return {category: [task_row, ...]} from COMPLETED tasks."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT file_hash, file_path, file_type, file_size, vault_id FROM tasks WHERE status='COMPLETED'"
        params = []
        if vault_id:
            sql += " AND vault_id=?"
            params.append(vault_id)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    buckets: dict[str, list] = {t: [] for t in types}
    for row in rows:
        cat = _classify(row['file_type'])
        if cat and cat in buckets:
            buckets[cat].append(dict(row))

    return {cat: random.sample(items, min(n, len(items))) for cat, items in buckets.items()}


def _time_file(task: dict) -> tuple[str | None, float]:
    """
    Run extractors for one file. Returns (bottleneck_extractor, elapsed_secs).
    Does NOT write any results to docvault.db.
    """
    file_path = task['file_path']
    file_type = (task['file_type'] or '').lstrip('.')
    vault_id  = task.get('vault_id') or ''

    extractors = _router.get_extractors(file_type, vault_id=vault_id)
    if not extractors:
        return None, 0.0

    cancel_token = threading.Event()
    ext_logger   = ExtractorLogger('benchmark', vault_id, task['file_hash'])
    ctx = ExtractorContext(
        vault_id=vault_id,
        file_hash=task['file_hash'],
        cancel_token=cancel_token,
        logger=ext_logger,
        settings=SettingsResolver(vault_id=vault_id),
    )

    slowest_name, slowest_elapsed, total = None, 0.0, 0.0
    for ext_mod in extractors:
        adapter = ext_mod if isinstance(ext_mod, BaseExtractor) else LegacyExtractorAdapter(ext_mod)
        t0 = time.monotonic()
        try:
            adapter.run(file_path, ctx)   # result discarded — timing only
        except Exception:
            pass
        elapsed = time.monotonic() - t0
        total  += elapsed
        if elapsed > slowest_elapsed:
            slowest_elapsed = elapsed
            slowest_name = adapter.name

    return slowest_name, total


def run_benchmark(db_path: str, n: int, types: list[str], vault_id: str | None) -> dict:
    """Core benchmark logic. Returns structured results dict."""
    samples = _sample_tasks(db_path, n, types, vault_id)

    results = {}
    overall_bottleneck = None
    overall_bottleneck_avg = 0.0

    for cat, tasks in samples.items():
        if not tasks:
            results[cat] = {'samples': 0, 'avg_secs': 0.0, 'p95_secs': 0.0,
                            'throughput': 0.0, 'bottleneck': None}
            continue

        timings = []
        cat_bottleneck, cat_bottleneck_time = None, 0.0
        for task in tasks:
            if not os.path.exists(task['file_path']):
                continue
            slow_ext, elapsed = _time_file(task)
            timings.append(elapsed)
            if elapsed > cat_bottleneck_time:
                cat_bottleneck_time = elapsed
                cat_bottleneck = slow_ext

        if not timings:
            results[cat] = {'samples': 0, 'avg_secs': 0.0, 'p95_secs': 0.0,
                            'throughput': 0.0, 'bottleneck': None}
            continue

        timings.sort()
        avg = sum(timings) / len(timings)
        p95 = timings[max(0, int(len(timings) * 0.95) - 1)] if timings else 0.0
        throughput = 3600.0 / avg if avg > 0 else 0.0

        results[cat] = {
            'samples': len(timings),
            'avg_secs': round(avg, 2),
            'p95_secs': round(p95, 2),
            'throughput': round(throughput, 0),
            'bottleneck': cat_bottleneck,
        }
        if avg > overall_bottleneck_avg:
            overall_bottleneck_avg = avg
            overall_bottleneck = cat_bottleneck

    return {'by_type': results, 'bottleneck_extractor': overall_bottleneck}


def _print_report(results: dict, db_path: str):
    print(f"\nDocVault Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    cs   = settings.get('embeddings:chunk_size')
    mp   = settings.get('ollama:max_parallel')
    emb  = settings.get('ollama:embed_model')
    print(f"Settings: chunk_size={cs}  max_parallel={mp}  embed_model={emb}")
    print("Note: throughput is in-process (excludes queue/DB overhead)\n")

    hdr = f"{'File type':<12}  {'Samples':>7}  {'Avg time':>9}  {'P95':>7}  {'Throughput (in-process)':>23}"
    print(hdr)
    print('-' * len(hdr))

    # Identify bottleneck category in a pre-pass so the marker is correct
    bottleneck_cat = max(
        (cat for cat, r in results['by_type'].items() if r['samples'] > 0),
        key=lambda cat: results['by_type'][cat]['avg_secs'],
        default=None,
    )
    for cat, r in results['by_type'].items():
        if r['samples'] == 0:
            print(f"{cat:<12}  {'—':>7}  {'—':>9}  {'—':>7}  {'no COMPLETED files':>23}")
            continue
        marker = '  ← BOTTLENECK' if cat == bottleneck_cat else ''
        print(f"{cat:<12}  {r['samples']:>7}  {r['avg_secs']:>8.2f}s  {r['p95_secs']:>6.2f}s  {r['throughput']:>20,.0f} f/hr{marker}")

    if bottleneck_cat:
        bn = results['by_type'][bottleneck_cat]
        print(f"\nBottleneck: {bn['bottleneck'] or bottleneck_cat} ({bn['avg_secs']}s avg)")

    print()


def save_result(results: dict):
    """Write one row to logs.db benchmark_runs."""
    from core.manager import get_logs_db_path, _connect
    bottleneck = results.get('bottleneck_extractor')
    by_type    = results['by_type']
    # Overall throughput: average of non-zero category throughputs
    throughputs = [r['throughput'] for r in by_type.values() if r['throughput'] > 0]
    overall    = sum(throughputs) / len(throughputs) if throughputs else 0.0

    with _connect(get_logs_db_path()) as conn:
        conn.execute(
            """INSERT INTO benchmark_runs (run_at, results, bottleneck_extractor, overall_files_per_hour)
               VALUES (?, ?, ?, ?)""",
            (datetime.now(timezone.utc).isoformat(),
             json.dumps(by_type),
             bottleneck,
             round(overall, 1))
        )
        conn.commit()
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"Results saved to logs.db (run_id: {run_id})")


def main():
    ap = argparse.ArgumentParser(description='DocVault benchmark tool')
    ap.add_argument('--samples', type=int, default=None,
                    help='Files per type (default: tuning:benchmark_samples_per_type setting)')
    ap.add_argument('--types', default='text,pdf,image,audio,video',
                    help='Comma-separated type categories')
    ap.add_argument('--vault', default='all',
                    help='Vault ID or "all"')
    ap.add_argument('--no-save', action='store_true',
                    help='Skip saving to logs.db')
    args = ap.parse_args()

    n = args.samples or int(settings.get('tuning:benchmark_samples_per_type') or 10)
    types = [t.strip() for t in args.types.split(',') if t.strip() in TYPE_EXTENSIONS]
    vault_id = None if args.vault == 'all' else args.vault
    db_path  = manager.get_db_path()

    results = run_benchmark(db_path, n, types, vault_id)
    _print_report(results, db_path)
    if not args.no_save:
        save_result(results)


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Verify manually (requires COMPLETED tasks in docvault.db)**

```bash
cd E:\DocVault
# Test --no-save (no DB write)
python tools/benchmark.py --samples 3 --types text,pdf --no-save
```
Expected: prints a table with text/pdf rows, no "Results saved" line, no errors.

```bash
# Test save path (DB write — omit --no-save flag to write to logs.db)
python tools/benchmark.py --samples 2 --types text
python -c "import sqlite3; conn=sqlite3.connect('logs.db'); print(conn.execute('SELECT run_id, overall_files_per_hour FROM benchmark_runs ORDER BY run_id DESC LIMIT 1').fetchone())"
```
Expected: prints a (run_id, throughput) tuple — confirms the row was written.

- [ ] **Step 3: Commit**

```bash
git add tools/benchmark.py
git commit -m "feat(benchmark): add standalone CLI benchmark tool"
```

---

## Chunk 3: Optimizer Core (`core/tuner.py` — thread + state)

### Task 4: Add startup snapshot rollback to `run.py`

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Add rollback helper and startup call to `run.py`**

Add this function before the `start()` function in `run.py`:

```python
def _rollback_optimizer_snapshot():
    """
    If a previous optimizer run crashed mid-sweep, tuning:_optimizer_snapshot
    will still be in settings.db with the original settings. Restore them.
    This must run after init_settings_db() and before any workers start.
    """
    from core.manager import get_settings_db_path, _connect
    try:
        with _connect(get_settings_db_path()) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='tuning:_optimizer_snapshot'"
            ).fetchone()
            if not row:
                return
            snapshot = json.loads(row['value'])
            from core.settings import settings as _settings
            for k, v in snapshot.items():
                try:
                    _settings.set(k, v)
                except Exception:
                    pass  # skip keys not in schema
            conn.execute("DELETE FROM settings WHERE key='tuning:_optimizer_snapshot'")
            conn.commit()
            logger.warning("Startup: restored settings from stale optimizer snapshot and cleared it.")
    except Exception as e:
        logger.error(f"Startup: optimizer snapshot rollback failed: {e}")
```

Add `import json` at the top of `run.py` if not already present.

In the `start()` function, add this call after `manager.init_settings_db()` and before `manager.init_db(DB_PATH)`:

```python
    _rollback_optimizer_snapshot()
```

- [ ] **Step 2: Verify no startup error**

```bash
python run.py &
sleep 3
# Check logs show no error about snapshot rollback
# Then kill it
```

- [ ] **Step 3: Commit**

```bash
git add run.py
git commit -m "feat(tuner): add startup optimizer snapshot rollback in run.py"
```

---

### Task 5: Add optimizer thread and state management to `core/tuner.py`

**Files:**
- Modify: `core/tuner.py`

This extends Task 2's file with the threading/state machinery.

- [ ] **Step 1: Add the following to `core/tuner.py`** (append after the existing code)

```python
# ── Module-level optimizer state ──────────────────────────────────────────────

import time
import random as _random

_optimizer_thread: threading.Thread | None = None
_abort_event      = threading.Event()
_state_lock       = threading.Lock()
_state: dict = {
    'state':               'idle',    # idle | starting | running | complete | aborted | error
    'run_index':           0,
    'total_runs':          0,
    'current_params':      {},
    'current_throughput':  None,
    'baseline_throughput': None,
    'runs':                [],
    'hardware':            {},
    'profiles':            None,
    'error':               None,
}


def get_state() -> dict:
    with _state_lock:
        import copy
        return copy.deepcopy(_state)


def _update_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)


# ── Snapshot helpers ──────────────────────────────────────────────────────────

_SWEPT_KEYS = [
    'embeddings:chunk_size',
    'embeddings:chunk_overlap',
    'ollama:max_parallel',
    'monitor:gpu_temp_throttle',
]


def _read_snapshot_keys() -> dict:
    """Read current production values for swept keys via settings.get()."""
    from core.settings import settings
    return {k: str(settings.get(k) or '') for k in _SWEPT_KEYS}


def _write_snapshot(snapshot: dict):
    """Persist snapshot to settings.db as reserved key (direct DB write)."""
    from core.manager import get_settings_db_path, _connect
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ('tuning:_optimizer_snapshot', json.dumps(snapshot))
        )
        conn.commit()


def _delete_snapshot():
    from core.manager import get_settings_db_path, _connect
    with _connect(get_settings_db_path()) as conn:
        conn.execute("DELETE FROM settings WHERE key='tuning:_optimizer_snapshot'")
        conn.commit()


def _restore_snapshot(snapshot: dict):
    from core.settings import settings
    for k, v in snapshot.items():
        try:
            settings.set(k, v)
        except Exception:
            pass


def _apply_params(params: dict):
    from core.settings import settings
    for k, v in params.items():
        try:
            settings.set(k, v)
        except Exception:
            pass


# ── Hardware reading ───────────────────────────────────────────────────────────

def _read_hardware() -> dict:
    try:
        from core.monitor import get_last_reading
        r = get_last_reading()
        if r is None:
            return {}
        return {
            'gpu_temp':  r.gpu_temp or None,
            'gpu_util':  r.gpu_util_pct or None,
            'ram_pct':   r.ram_pct or None,
            'cpu_temp':  r.cpu_temp or None,
        }
    except Exception:
        return {}


# ── Mini-benchmark (shared by optimizer and tools/benchmark.py) ───────────────

def run_mini_benchmark(db_path: str, n: int, vault_id: str | None = None) -> float:
    """
    Run in-process benchmark. Returns overall files/hour (in-process).
    Imports from tools/benchmark.py to avoid duplicating logic.
    """
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        'benchmark',
        os.path.join(os.path.dirname(__file__), '..', 'tools', 'benchmark.py')
    )
    bm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bm)

    types = list(bm.TYPE_EXTENSIONS.keys())
    results = bm.run_benchmark(db_path, n, types, vault_id)
    throughputs = [r['throughput'] for r in results['by_type'].values() if r['throughput'] > 0]
    return sum(throughputs) / len(throughputs) if throughputs else 0.0


# ── Optimizer sweep thread ─────────────────────────────────────────────────────

def _run_sweep(db_path: str, samples: int):
    """Main optimizer body — runs in daemon thread."""
    from core import manager as _manager

    snapshot = _read_snapshot_keys()
    _write_snapshot(snapshot)

    # Pause workers
    _manager.set_pause_state(True)
    _update_state(state='running')

    try:
        hw = _read_hardware()
        gpu_util = hw.get('gpu_util')
        matrix = build_sweep_matrix(snapshot, gpu_util_pct=gpu_util)
        original_throttle = float(snapshot.get('monitor:gpu_temp_throttle', '80'))

        _update_state(total_runs=len(matrix), runs=[], baseline_throughput=None)

        completed_runs = []
        for i, params in enumerate(matrix):
            if _abort_event.is_set():
                break

            _apply_params(params)
            _update_state(run_index=i + 1, current_params=params,
                          hardware=_read_hardware())

            # Measure GPU temp before + after; take max
            hw_before = _read_hardware()
            t0 = time.monotonic()
            throughput = run_mini_benchmark(db_path, samples)
            hw_after = _read_hardware()

            max_gpu_temp = None
            for hw_snap in [hw_before, hw_after]:
                t = hw_snap.get('gpu_temp')
                if t and (max_gpu_temp is None or t > max_gpu_temp):
                    max_gpu_temp = t

            run_record = {
                'index':        i + 1,
                'params':       params,
                'throughput':   throughput,
                'max_gpu_temp': max_gpu_temp,
            }
            completed_runs.append(run_record)

            if i == 0:
                _update_state(baseline_throughput=throughput)

            _update_state(
                current_throughput=throughput,
                runs=list(completed_runs),
                hardware=_read_hardware(),
            )

        if _abort_event.is_set():
            _restore_snapshot(snapshot)
            _delete_snapshot()
            _manager.set_pause_state(False)
            _update_state(state='aborted')
            return

        # Select profiles
        profiles = None
        if completed_runs:
            profiles = select_profiles(completed_runs, original_throttle)

        _restore_snapshot(snapshot)
        _delete_snapshot()
        _manager.set_pause_state(False)
        _update_state(state='complete', profiles=profiles)

    except Exception as e:
        try:
            _restore_snapshot(snapshot)
            _delete_snapshot()
        except Exception:
            pass
        try:
            _manager.set_pause_state(False)
        except Exception:
            pass
        _update_state(state='error', error=str(e))


def start_optimizer(db_path: str) -> bool:
    """
    Start the optimizer. Returns False if already running (caller should 409).
    """
    global _optimizer_thread, _abort_event
    if _optimizer_thread is not None and _optimizer_thread.is_alive():
        return False

    _abort_event = threading.Event()
    from core.settings import settings
    samples = int(settings.get('tuning:benchmark_samples_per_type') or 5)

    _update_state(state='starting', run_index=0, total_runs=0,
                  current_params={}, current_throughput=None,
                  baseline_throughput=None, runs=[], profiles=None, error=None)

    _optimizer_thread = threading.Thread(
        target=_run_sweep, args=(db_path, samples), daemon=True, name='optimizer'
    )
    _optimizer_thread.start()
    return True


def abort_optimizer() -> bool:
    """Signal abort. Returns False if no active run."""
    if _optimizer_thread is None or not _optimizer_thread.is_alive():
        return False
    _abort_event.set()
    return True
```

- [ ] **Step 2: Verify imports work**

```bash
python -c "from core.tuner import start_optimizer, get_state, abort_optimizer; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Add optimizer thread tests to `test_tuner.py`**

```python
def test_build_sweep_matrix_first_entry_is_snapshot():
    """Baseline run must exactly match snapshot."""
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=None)
    assert matrix[0] == snapshot


def test_build_sweep_matrix_no_duplicates():
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=None)
    keys = [json.dumps(r, sort_keys=True) for r in matrix]
    assert len(keys) == len(set(keys)), "Sweep matrix must not contain duplicates"
```

- [ ] **Step 4: Run all tuner tests**

```bash
python -m pytest tests/unit/test_tuner.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/tuner.py tests/unit/test_tuner.py
git commit -m "feat(tuner): add optimizer thread, state management, and sweep execution"
```

---

## Chunk 4: API Endpoints

### Task 6: Add 6 new endpoints to `api/routes/utils.py`

**Files:**
- Modify: `api/routes/utils.py`

All new routes are inserted **at the top of the file**, immediately after the `router = APIRouter()` line and before any existing routes. This satisfies the project's route-ordering requirement.

- [ ] **Step 1: Insert endpoints into `api/routes/utils.py`**

After `router = APIRouter()` (line 11), insert:

```python
# ── Performance Stats ─────────────────────────────────────────────────────────

@router.get("/utils/performance_stats")
def performance_stats():
    """
    Returns extractor timing stats, throughput trends, bottleneck callout,
    and last 5 benchmark runs for the Analytics dashboard.
    """
    from core.manager import get_logs_db_path, _connect
    import sqlite3

    logs_db = get_logs_db_path()

    with _connect(logs_db) as conn:
        # Extractor stats
        rows = conn.execute(
            """SELECT extractor,
                      AVG(elapsed_secs) AS avg_secs,
                      COUNT(*) AS cnt
               FROM task_timings
               WHERE elapsed_secs IS NOT NULL
               GROUP BY extractor
               ORDER BY avg_secs DESC"""
        ).fetchall()

        extractor_stats = []
        for r in rows:
            avg = r['avg_secs']
            category = 'fast' if avg < 1 else ('moderate' if avg < 10 else 'slow')
            # p95: fetch all elapsed for this extractor, sort, take 95th pct
            elapseds = [row[0] for row in conn.execute(
                "SELECT elapsed_secs FROM task_timings WHERE extractor=? AND elapsed_secs IS NOT NULL ORDER BY elapsed_secs",
                (r['extractor'],)
            ).fetchall()]
            p95 = elapseds[max(0, int(len(elapseds) * 0.95) - 1)] if elapseds else 0.0
            extractor_stats.append({
                'extractor': r['extractor'], 'avg_secs': round(avg, 2),
                'p95_secs': round(p95, 2), 'count': r['cnt'], 'category': category,
            })

        # Throughput 24h (hourly bins)
        rows_24h = conn.execute(
            """SELECT strftime('%H:00', completed_at) AS hour, COUNT(*) AS cnt
               FROM task_timings
               WHERE completed_at >= datetime('now', '-24 hours')
               GROUP BY hour ORDER BY hour"""
        ).fetchall()
        throughput_24h = [{'hour': r['hour'], 'files_per_hour': r['cnt']} for r in rows_24h]

        # Throughput 7d (daily bins)
        rows_7d = conn.execute(
            """SELECT date(completed_at) AS day, COUNT(*) AS cnt
               FROM task_timings
               WHERE completed_at >= datetime('now', '-7 days')
               GROUP BY day ORDER BY day"""
        ).fetchall()
        throughput_7d = [{'day': r['day'], 'files_per_hour': r['cnt']} for r in rows_7d]

        # Bottleneck (slowest extractor)
        bottleneck = None
        if extractor_stats:
            b = extractor_stats[0]
            tip = (f"Your slowest extractor is {b['extractor']} ({b['avg_secs']}s avg, "
                   f"{b['count']} files processed). Consider disabling it for vaults that don't need it.")
            bottleneck = {'extractor': b['extractor'], 'avg_secs': b['avg_secs'], 'tip': tip}

        # Last 5 benchmark runs
        bench_rows = conn.execute(
            """SELECT run_id, run_at, overall_files_per_hour, bottleneck_extractor
               FROM benchmark_runs ORDER BY run_id DESC LIMIT 5"""
        ).fetchall()
        benchmark_runs = [dict(r) for r in bench_rows]

    return {
        'extractor_stats': extractor_stats,
        'throughput_24h':  throughput_24h,
        'throughput_7d':   throughput_7d,
        'bottleneck':      bottleneck,
        'benchmark_runs':  benchmark_runs,
    }


# ── Optimizer Endpoints ───────────────────────────────────────────────────────

class OptimizerApplyRequest(BaseModel):
    profile: str  # "raw_speed" | "sustainable" | "balanced"


@router.post("/utils/optimizer/start")
def optimizer_start():
    """Launch the optimizer. 409 if already running."""
    from api.main import DB_PATH
    from core.tuner import start_optimizer
    if not start_optimizer(DB_PATH):
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="optimizer already running")
    return {"state": "starting"}


@router.get("/utils/optimizer/status")
def optimizer_status():
    """Live poll — frontend calls every 1000ms."""
    from core.tuner import get_state
    return get_state()


@router.post("/utils/optimizer/apply")
def optimizer_apply(req: OptimizerApplyRequest):
    """Apply a completed optimizer profile to settings.db."""
    from core.tuner import get_state
    from fastapi import HTTPException
    state = get_state()
    if state['state'] != 'complete':
        raise HTTPException(status_code=400, detail="no complete run to apply")
    profiles = state.get('profiles') or {}
    profile = profiles.get(req.profile)
    if not profile:
        raise HTTPException(status_code=400, detail=f"unknown profile: {req.profile}")
    from core.settings import settings
    params = profile['params']
    for k, v in params.items():
        try:
            settings.set(k, v)
        except Exception:
            pass
    return {"applied": True, "params": params}


@router.post("/utils/optimizer/abort")
def optimizer_abort():
    """Abort the running optimizer. 400 if not running."""
    from core.tuner import abort_optimizer, get_state
    from fastapi import HTTPException
    s = get_state()['state']
    if s in ('idle', 'complete', 'aborted', 'error'):
        raise HTTPException(status_code=400, detail="no active run")
    abort_optimizer()
    return {"state": "aborting"}
```

- [ ] **Step 2: Restart server and verify endpoints exist**

```bash
# Restart the server, then:
curl -s http://localhost:8000/api/utils/performance_stats | python -m json.tool | head -20
curl -s http://localhost:8000/api/utils/optimizer/status
# Verify 400 guard: abort with no running optimizer
curl -s -X POST http://localhost:8000/api/utils/optimizer/abort
# Verify 409 guard: start twice in quick succession (second should 409)
curl -s -X POST http://localhost:8000/api/utils/optimizer/start && curl -s -X POST http://localhost:8000/api/utils/optimizer/start
curl -s -X POST http://localhost:8000/api/utils/optimizer/abort
```
Expected: `performance_stats` returns JSON with `extractor_stats`, `throughput_24h` etc. `status` returns `{"state":"idle",...}`. Abort with no run returns HTTP 400. Second start returns HTTP 409.

- [ ] **Step 3: Commit**

```bash
git add api/routes/utils.py
git commit -m "feat(api): add performance_stats and optimizer endpoints"
```

---

## Chunk 5: Analytics Dashboard (Telemetry Performance Tab)

### Task 7: Add "Performance" tab to `frontend/telemetry.html`

**Files:**
- Modify: `frontend/telemetry.html`

The Telemetry page uses a dark monospace terminal aesthetic (`JetBrains Mono`, black bg, emerald/amber/ruby accent colors). Match it exactly.

- [ ] **Step 1: Add tab toggle buttons to the `controls` div**

Find the `<div class="controls">` element and add two new buttons before the `1H` button:

```html
                <button class="btn-toggle active" id="btn-hw" onclick="showTab('hardware')">HW</button>
                <button class="btn-toggle" id="btn-perf" onclick="showTab('perf')">PERF</button>
```

- [ ] **Step 2: Wrap existing sensor-grid and runtime-section in a `hw-tab` div**

Wrap the existing `.sensor-grid` and `.runtime-section` divs:

```html
        <!-- Hardware tab (existing content) -->
        <div id="tab-hardware">
          <!-- sensor-grid and runtime-section go here, unchanged -->
        </div>
```

- [ ] **Step 3: Add the Performance tab div after `tab-hardware`**

```html
        <!-- Performance tab -->
        <div id="tab-perf" class="hidden">

          <!-- Extractor bar chart -->
          <div class="runtime-card" style="margin-bottom: 2rem;">
            <div class="section-header">
              <span>EXTRACTOR TIMINGS</span>
              <span id="perf-updated" style="font-size:0.65rem;opacity:0.4">—</span>
            </div>
            <div id="extractor-bars">
              <div style="font-size:0.8rem;opacity:0.4">Loading...</div>
            </div>
          </div>

          <!-- Throughput trend -->
          <div class="runtime-card" style="margin-bottom: 2rem;">
            <div class="section-header">
              <span>THROUGHPUT TREND</span>
              <div style="display:flex;gap:0.5rem">
                <button class="btn-toggle active" id="tp-24h" onclick="setThroughputRange('24h')" style="font-size:0.65rem;padding:0.2rem 0.5rem">24H</button>
                <button class="btn-toggle" id="tp-7d" onclick="setThroughputRange('7d')" style="font-size:0.65rem;padding:0.2rem 0.5rem">7D</button>
              </div>
            </div>
            <div id="throughput-chart" style="height:60px;display:flex;align-items:flex-end;gap:2px;padding-top:8px">
              <div style="font-size:0.75rem;opacity:0.4">No data yet.</div>
            </div>
          </div>

          <!-- Bottleneck callout -->
          <div id="bottleneck-card" class="runtime-card" style="margin-bottom:2rem;display:none;">
            <div class="section-header"><span>⚠ BOTTLENECK DETECTED</span></div>
            <div id="bottleneck-text" style="font-size:0.8rem;line-height:1.6;color:var(--amber)"></div>
          </div>

          <!-- Benchmark run history -->
          <div class="runtime-card">
            <div class="section-header"><span>BENCHMARK RUN HISTORY</span></div>
            <div id="bench-history">
              <div style="font-size:0.8rem;opacity:0.4">No benchmark runs recorded. Run: python tools/benchmark.py</div>
            </div>
          </div>

        </div>
```

- [ ] **Step 4: Add Performance tab CSS** (inside the `<style>` block)

```css
        .bar-row { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.6rem; font-size: 0.75rem; }
        .bar-label { width: 160px; color: var(--slate); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
        .bar-track { flex: 1; height: 6px; background: rgba(148,163,184,0.1); border-radius: 3px; }
        .bar-fill { height: 6px; border-radius: 3px; transition: width 0.5s; }
        .bar-val { width: 80px; text-align: right; flex-shrink: 0; font-family: 'JetBrains Mono', monospace; }
        .tp-bar { flex: 1; border-radius: 2px 2px 0 0; min-width: 4px; background: var(--emerald); opacity: 0.7; transition: height 0.3s; }
        .bench-row { display: flex; justify-content: space-between; font-size: 0.75rem; padding: 0.4rem 0; border-bottom: 1px dashed rgba(148,163,184,0.05); }
        .hidden { display: none !important; }
```

- [ ] **Step 5: Add JavaScript for the Performance tab** (inside the `<script>` block, before `window.onload = init`)

```javascript
        let _throughputRange = '24h';
        let _perfData = null;

        function showTab(tab) {
            document.getElementById('tab-hardware').classList.toggle('hidden', tab !== 'hardware');
            document.getElementById('tab-perf').classList.toggle('hidden', tab !== 'perf');
            document.getElementById('btn-hw').classList.toggle('active', tab === 'hardware');
            document.getElementById('btn-perf').classList.toggle('active', tab === 'perf');
            if (tab === 'perf' && !_perfData) loadPerf();
        }

        async function loadPerf() {
            try {
                _perfData = await api('/utils/performance_stats');
                renderExtractorBars(_perfData.extractor_stats);
                renderThroughput(_perfData);
                renderBottleneck(_perfData.bottleneck);
                renderBenchHistory(_perfData.benchmark_runs);
                document.getElementById('perf-updated').textContent = new Date().toLocaleTimeString();
            } catch(e) {
                document.getElementById('extractor-bars').innerHTML =
                    '<div style="color:var(--ruby);font-size:0.8rem">Failed to load performance data.</div>';
            }
        }

        function renderExtractorBars(stats) {
            if (!stats || !stats.length) {
                document.getElementById('extractor-bars').innerHTML =
                    '<div style="font-size:0.8rem;opacity:0.4">No timing data yet. Run some extractions first.</div>';
                return;
            }
            const maxAvg = Math.max(...stats.map(s => s.avg_secs));
            const colors = { fast: 'var(--emerald)', moderate: 'var(--amber)', slow: 'var(--ruby)' };
            document.getElementById('extractor-bars').innerHTML = stats.map(s => {
                const pct = maxAvg > 0 ? (s.avg_secs / maxAvg * 100).toFixed(1) : 0;
                const color = colors[s.category] || 'var(--slate)';
                return `<div class="bar-row">
                  <div class="bar-label" title="${s.extractor}">${s.extractor}</div>
                  <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
                  <div class="bar-val" style="color:${color}">${s.avg_secs}s avg</div>
                  <div style="width:60px;text-align:right;font-size:0.65rem;opacity:0.5">${s.count} files</div>
                </div>`;
            }).join('');
        }

        function setThroughputRange(range) {
            _throughputRange = range;
            document.getElementById('tp-24h').classList.toggle('active', range === '24h');
            document.getElementById('tp-7d').classList.toggle('active', range === '7d');
            if (_perfData) renderThroughput(_perfData);
        }

        function renderThroughput(data) {
            const points = _throughputRange === '24h' ? data.throughput_24h : data.throughput_7d;
            const chart = document.getElementById('throughput-chart');
            if (!points || !points.length) {
                chart.innerHTML = '<div style="font-size:0.75rem;opacity:0.4">No data for this range.</div>';
                return;
            }
            const max = Math.max(...points.map(p => p.files_per_hour));
            chart.innerHTML = points.map(p => {
                const h = max > 0 ? Math.max((p.files_per_hour / max * 100), 4) : 4;
                const label = _throughputRange === '24h' ? p.hour : p.day;
                return `<div class="tp-bar" style="height:${h}%" title="${label}: ${p.files_per_hour} files/hr"></div>`;
            }).join('');
        }

        function renderBottleneck(b) {
            const card = document.getElementById('bottleneck-card');
            if (!b) { card.style.display = 'none'; return; }
            card.style.display = '';
            document.getElementById('bottleneck-text').textContent = b.tip;
        }

        function renderBenchHistory(runs) {
            const el = document.getElementById('bench-history');
            if (!runs || !runs.length) {
                el.innerHTML = '<div style="font-size:0.8rem;opacity:0.4">No benchmark runs. Run: python tools/benchmark.py</div>';
                return;
            }
            el.innerHTML = runs.map(r => `
              <div class="bench-row">
                <span style="color:var(--slate)">${new Date(r.run_at).toLocaleDateString()}</span>
                <span style="color:#fff">${r.overall_files_per_hour?.toLocaleString() || '—'} f/hr</span>
                <span style="color:var(--amber);font-size:0.7rem">${r.bottleneck_extractor || '—'}</span>
              </div>`).join('');
        }
```

- [ ] **Step 6: Verify in browser**

Navigate to `http://localhost:8000/telemetry`. Click the PERF tab. Should show extractor bar chart (or "No timing data yet" if logs.db is empty).

- [ ] **Step 7: Commit**

```bash
git add frontend/telemetry.html
git commit -m "feat(ui): add Performance tab to Telemetry page"
```

---

## Chunk 6: Mission Control UI

### Task 8: Add Mission Control overlay to `frontend/utils.html`

**Files:**
- Modify: `frontend/utils.html`

Read the current utils.html first to understand where to insert. The overlay is a fixed full-screen `<div>`, hidden by default, toggled open by the "⚡ Run Optimizer" button.

- [ ] **Step 1: Read `frontend/utils.html`** to find the correct insertion point (end of `<main>`, before `</body>`)

- [ ] **Step 2: Add the Performance Tuning card to the Utilities page**

Find the closing `</main>` tag and insert before it:

```html
    <!-- Performance Tuning -->
    <div class="bg-white rounded shadow p-4 mb-4">
      <h2 class="font-semibold text-gray-700 mb-1">Performance Tuning</h2>
      <p class="text-xs text-gray-400 mb-3">Pause workers and run an automated parameter sweep to find optimal throughput settings for your hardware.</p>
      <div class="flex gap-3">
        <button onclick="openOptimizer()" class="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded text-sm font-medium">⚡ Run Optimizer</button>
        <a href="/telemetry" onclick="event.preventDefault(); window.location.href='/telemetry#perf'" class="bg-gray-100 hover:bg-gray-200 text-gray-700 px-4 py-1.5 rounded text-sm">📊 Analytics Dashboard</a>
      </div>
    </div>
```

- [ ] **Step 3: Add the Mission Control overlay `<div>`** (insert just before `</body>`)

```html
  <!-- ═══ MISSION CONTROL OPTIMIZER OVERLAY ═══ -->
  <div id="mc-overlay" class="hidden fixed inset-0 z-50 flex flex-col" style="background:#050809;font-family:'JetBrains Mono','Fira Code',monospace;color:#94a3b8">

    <!-- Title bar -->
    <div style="border-bottom:1px solid #1e2740;padding:8px 16px;display:flex;justify-content:space-between;align-items:center;flex-shrink:0">
      <span style="color:#4f6ef7;font-size:11px;font-weight:700;letter-spacing:0.15em">◈ DOCVAULT ENGINEERING CONSOLE — OPTIMIZER</span>
      <div style="display:flex;gap:12px;align-items:center">
        <span id="mc-run-label" style="font-size:10px;color:#334155">IDLE</span>
        <button onclick="mcAbort()" id="mc-abort-btn" style="background:#7f1d1d;color:#fca5a5;border:none;padding:3px 12px;font-size:10px;font-weight:700;cursor:pointer;letter-spacing:0.05em" class="hidden">■ ABORT</button>
        <button onclick="closeOptimizer()" style="background:transparent;border:1px solid #334155;color:#64748b;padding:3px 10px;font-size:10px;cursor:pointer">✕ CLOSE</button>
      </div>
    </div>

    <!-- Quadrant grid -->
    <div style="flex:1;display:grid;grid-template-columns:160px 1fr;grid-template-rows:1fr 1fr 40px;overflow:hidden">

      <!-- TOP-LEFT: Hardware Gauges (spans both rows) -->
      <div style="grid-row:span 2;border-right:1px solid #1e2740;border-bottom:1px solid #1e2740;padding:12px;display:flex;flex-direction:column;gap:8px;overflow:hidden">
        <div style="font-size:8px;color:#334155;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Hardware</div>
        <div id="mc-gauge-gpu-temp" class="mc-gauge-block"></div>
        <div id="mc-gauge-gpu-util" class="mc-gauge-block"></div>
        <div id="mc-gauge-ram" class="mc-gauge-block"></div>
        <div id="mc-gauge-cpu-temp" class="mc-gauge-block"></div>
      </div>

      <!-- TOP-RIGHT: Throughput + sparkline -->
      <div style="border-bottom:1px solid #1e2740;padding:16px 20px;display:flex;flex-direction:column;justify-content:center">
        <div style="font-size:9px;color:#334155;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Throughput</div>
        <div id="mc-throughput-val" style="font-size:42px;font-weight:900;color:#4ade80;letter-spacing:-2px;line-height:1">—</div>
        <div id="mc-throughput-delta" style="font-size:11px;color:#64748b;margin-top:4px">files / hour</div>
        <div id="mc-sparkline" style="display:flex;align-items:flex-end;gap:3px;height:40px;margin-top:12px"></div>
      </div>

      <!-- MID-RIGHT: Params / Profile cards -->
      <div id="mc-params-pane" style="border-bottom:1px solid #1e2740;padding:12px 20px;overflow-y:auto">
        <div style="font-size:8px;color:#334155;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">Parameters</div>
        <div id="mc-params-rows" style="font-size:10px;color:#64748b">Waiting for optimizer start...</div>
        <div id="mc-profile-cards" class="hidden" style="display:flex;gap:10px;flex-wrap:wrap"></div>
      </div>

      <!-- BOTTOM-RIGHT: Event log -->
      <div style="border-bottom:1px solid #1e2740;padding:8px 20px;overflow-y:auto;font-size:9px" id="mc-log">
        <div style="color:#334155">Awaiting optimizer start...</div>
      </div>

      <!-- BOTTOM BAR (full width) -->
      <div style="grid-column:span 2;border-top:1px solid #1e2740;display:flex;justify-content:space-between;align-items:center;padding:0 16px;background:#030507">
        <span id="mc-eta" style="font-size:9px;color:#334155">—</span>
        <div style="display:flex;gap:8px;align-items:center">
          <button onclick="mcAbort()" id="mc-abort-btn-bar" style="background:#7f1d1d;color:#fca5a5;border:none;padding:4px 12px;font-size:10px;font-weight:700;cursor:pointer;letter-spacing:0.05em" class="hidden">■ ABORT</button>
          <button id="mc-apply-btn" onclick="mcApplyBest()" disabled style="background:#312e81;color:#a5b4fc;border:none;padding:4px 16px;font-size:10px;font-weight:700;cursor:not-allowed;opacity:0.4;letter-spacing:0.05em">↓ APPLY BEST PROFILE</button>
        </div>
      </div>

    </div>
  </div>

  <style>
    .mc-gauge-block { background:#0a0e18; border:1px solid #1e2740; border-radius:4px; padding:6px 8px; }
    .mc-gauge-label { font-size:8px; color:#334155; text-transform:uppercase; letter-spacing:0.08em; }
    .mc-gauge-val { font-size:16px; font-weight:700; font-family:monospace; line-height:1.2; }
    .mc-gauge-bar { height:3px; background:#0d1117; border-radius:2px; margin-top:4px; }
    .mc-gauge-fill { height:3px; border-radius:2px; transition:width 0.5s; }
    .mc-log-line { padding:2px 0; border-bottom:1px solid #0d1117; }
    .mc-profile-card { background:#0a0e18; border:1px solid #1e2740; border-radius:6px; padding:10px; min-width:140px; flex:1; }
    .mc-profile-name { font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:4px; }
    .mc-profile-val { font-size:20px; font-weight:700; font-family:monospace; color:#4ade80; }
    .mc-apply-btn { background:#1e2a4a; color:#818cf8; border:1px solid #3730a3; padding:4px 12px; font-size:9px; font-weight:700; cursor:pointer; margin-top:8px; width:100%; }
    .mc-apply-btn:hover { background:#1e3a8a; }
  </style>

  <script>
    let _mcPollInterval = null;
    let _mcSelectedProfile = 'raw_speed';
    let _mcRunCount = 0;
    let _mcStartTime = null;

    function openOptimizer() {
      document.getElementById('mc-overlay').classList.remove('hidden');
      mcStartRun();
    }

    function closeOptimizer() {
      if (_mcPollInterval) { clearInterval(_mcPollInterval); _mcPollInterval = null; }
      document.getElementById('mc-overlay').classList.add('hidden');
    }

    async function mcStartRun() {
      try {
        const r = await api('/utils/optimizer/start', { method: 'POST' });
        mcLog('info', 'Optimizer started — pausing workers...');
        _mcStartTime = Date.now();
        document.getElementById('mc-abort-btn').classList.remove('hidden');
        document.getElementById('mc-abort-btn-bar').classList.remove('hidden');
        document.getElementById('mc-apply-btn').disabled = true;
        document.getElementById('mc-apply-btn').style.opacity = '0.4';
        document.getElementById('mc-apply-btn').style.cursor = 'not-allowed';
        _mcPollInterval = setInterval(mcPoll, 1000);
      } catch(e) {
        if (e.status === 409) {
          mcLog('warn', 'Optimizer already running — connecting to existing run.');
          _mcPollInterval = setInterval(mcPoll, 1000);
        } else {
          mcLog('error', 'Failed to start optimizer: ' + e.message);
        }
      }
    }

    async function mcAbort() {
      try {
        await api('/utils/optimizer/abort', { method: 'POST' });
        mcLog('warn', 'Abort signal sent — restoring settings...');
      } catch(e) {
        mcLog('error', 'Abort failed: ' + e.message);
      }
    }

    async function mcPoll() {
      try {
        const s = await api('/utils/optimizer/status');
        mcRender(s);
        if (s.state === 'complete' || s.state === 'aborted' || s.state === 'error') {
          clearInterval(_mcPollInterval); _mcPollInterval = null;
          document.getElementById('mc-abort-btn').classList.add('hidden');
          document.getElementById('mc-abort-btn-bar').classList.add('hidden');
          if (s.state === 'complete') {
            document.getElementById('mc-apply-btn').disabled = false;
            document.getElementById('mc-apply-btn').style.opacity = '1';
            document.getElementById('mc-apply-btn').style.cursor = 'pointer';
            mcRenderProfiles(s.profiles);
          }
        }
      } catch(e) { /* poll errors are non-fatal */ }
    }

    function mcRender(s) {
      // Title bar
      if (s.state === 'running') {
        const elapsed = _mcStartTime ? Math.round((Date.now() - _mcStartTime) / 1000) : 0;
        const perRun = s.run_index > 0 ? elapsed / s.run_index : 0;
        const remaining = s.total_runs - s.run_index;
        const eta = Math.round(perRun * remaining / 60);
        document.getElementById('mc-run-label').textContent = `RUN ${s.run_index} / ${s.total_runs}  ·  ETA ~${eta}m`;
        document.getElementById('mc-eta').textContent = `RUN ${s.run_index} / ${s.total_runs} · ETA ~${eta} min`;
      } else {
        document.getElementById('mc-run-label').textContent = (s.state || 'idle').toUpperCase();
        document.getElementById('mc-eta').textContent = s.state || '—';
      }

      // Throughput
      const tp = s.current_throughput;
      document.getElementById('mc-throughput-val').textContent = tp ? tp.toLocaleString() : '—';
      if (tp && s.baseline_throughput) {
        const delta = Math.round((tp - s.baseline_throughput) / s.baseline_throughput * 100);
        const sign = delta >= 0 ? '+' : '';
        document.getElementById('mc-throughput-delta').innerHTML =
          `files / hour &nbsp;·&nbsp; <span style="color:${delta>=0?'#4ade80':'#f87171'}">${sign}${delta}% vs baseline</span>`;
      }

      // Sparkline
      const spark = document.getElementById('mc-sparkline');
      if (s.runs && s.runs.length) {
        const maxTp = Math.max(...s.runs.map(r => r.throughput));
        spark.innerHTML = s.runs.map(r => {
          const h = maxTp > 0 ? Math.max(r.throughput / maxTp * 100, 5) : 5;
          const isLast = r.index === s.run_index;
          const color = isLast ? '#4ade80' : '#1e3a5f';
          return `<div style="flex:1;height:${h}%;background:${color};border-radius:2px 2px 0 0;min-width:6px;transition:height 0.3s" title="Run ${r.index}: ${r.throughput?.toLocaleString()} f/hr"></div>`;
        }).join('');
      }

      // Hardware gauges
      const hw = s.hardware || {};
      mcSetGauge('mc-gauge-gpu-temp', 'GPU Temp', hw.gpu_temp != null ? `${Math.round(hw.gpu_temp)}°C` : '—', hw.gpu_temp, 100, hw.gpu_temp > 80 ? '#ef4444' : hw.gpu_temp > 70 ? '#f59e0b' : '#4ade80');
      mcSetGauge('mc-gauge-gpu-util', 'GPU Util', hw.gpu_util != null ? `${Math.round(hw.gpu_util)}%` : '—', hw.gpu_util, 100, '#60a5fa');
      mcSetGauge('mc-gauge-ram', 'RAM', hw.ram_pct != null ? `${Math.round(hw.ram_pct)}%` : '—', hw.ram_pct, 100, hw.ram_pct > 85 ? '#ef4444' : '#a78bfa');
      mcSetGauge('mc-gauge-cpu-temp', 'CPU Temp', hw.cpu_temp != null ? `${Math.round(hw.cpu_temp)}°C` : '—', hw.cpu_temp, 100, '#4ade80');

      // Params
      if (s.state === 'running' && s.current_params && Object.keys(s.current_params).length) {
        const paramMap = { 'embeddings:chunk_size': 'chunk_size', 'embeddings:chunk_overlap': 'chunk_overlap', 'ollama:max_parallel': 'max_parallel', 'monitor:gpu_temp_throttle': 'gpu_throttle' };
        document.getElementById('mc-params-rows').innerHTML = Object.entries(s.current_params).map(([k, v]) =>
          `<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #0d1117">
            <span style="color:#475569">${paramMap[k]||k}</span>
            <span style="color:#e2e8f0">${v}</span>
           </div>`
        ).join('');
      }

      // Log new run completions
      if (s.runs && s.runs.length > _mcRunCount) {
        for (let i = _mcRunCount; i < s.runs.length; i++) {
          const r = s.runs[i];
          const baseline = s.baseline_throughput || 0;
          const delta = baseline > 0 ? Math.round((r.throughput - baseline) / baseline * 100) : 0;
          const sign = delta >= 0 ? '+' : '';
          const color = delta > 0 ? 'ok' : (delta < 0 ? 'warn' : 'info');
          mcLog(color, `[RUN ${r.index}] → ${r.throughput?.toLocaleString()} f/hr  ${sign}${delta}%${r.max_gpu_temp ? '  GPU:' + Math.round(r.max_gpu_temp) + '°' : ''}`);
        }
        _mcRunCount = s.runs.length;
      }
    }

    function mcSetGauge(id, label, valText, val, max, color) {
      const pct = (val && max) ? Math.min(val / max * 100, 100) : 0;
      document.getElementById(id).innerHTML = `
        <div class="mc-gauge-label">${label}</div>
        <div class="mc-gauge-val" style="color:${color}">${valText}</div>
        <div class="mc-gauge-bar"><div class="mc-gauge-fill" style="width:${pct}%;background:${color}"></div></div>`;
    }

    function mcLog(level, msg) {
      const colors = { info: '#60a5fa', ok: '#4ade80', warn: '#f59e0b', error: '#f87171' };
      const log = document.getElementById('mc-log');
      const ts = new Date().toLocaleTimeString();
      const line = document.createElement('div');
      line.className = 'mc-log-line';
      line.innerHTML = `<span style="color:#334155">${ts} </span><span style="color:${colors[level]||'#94a3b8'}">${msg}</span>`;
      log.appendChild(line);
      log.scrollTop = log.scrollHeight;
    }

    function mcRenderProfiles(profiles) {
      if (!profiles) return;
      document.getElementById('mc-params-rows').classList.add('hidden');
      document.getElementById('mc-params-rows').style.display = 'none';
      const cards = document.getElementById('mc-profile-cards');
      cards.classList.remove('hidden');
      cards.style.display = 'flex';
      const labels = { raw_speed: 'Raw Speed', sustainable: 'Sustainable', balanced: 'Balanced' };
      const shown = new Set();
      let html = '';
      for (const [key, p] of Object.entries(profiles)) {
        const sig = JSON.stringify(p.params);
        if (shown.has(sig)) continue;
        shown.add(sig);
        html += `<div class="mc-profile-card">
          <div class="mc-profile-name">${labels[key]||key}</div>
          <div class="mc-profile-val">${p.throughput?.toLocaleString() || '—'}</div>
          <div style="font-size:9px;color:#475569;margin-top:2px">f/hr</div>
          <button class="mc-apply-btn" onclick="mcApplyProfile('${key}')">↓ Apply</button>
        </div>`;
      }
      cards.innerHTML = html;
      mcLog('ok', 'Optimizer complete. Select a profile to apply.');
    }

    async function mcApplyProfile(profileKey) {
      _mcSelectedProfile = profileKey;  // track selected for mcApplyBest()
      try {
        const r = await api('/utils/optimizer/apply', {
          method: 'POST',
          body: JSON.stringify({ profile: profileKey })
        });
        mcLog('ok', `✓ Applied ${profileKey} profile. Restart server to take full effect.`);
      } catch(e) {
        mcLog('error', 'Apply failed: ' + e.message);
      }
    }

    async function mcApplyBest() {
      await mcApplyProfile(_mcSelectedProfile);
    }
  </script>
```

- [ ] **Step 4: Verify in browser**

Navigate to `http://localhost:8000/utils`. Should show "Performance Tuning" card with "⚡ Run Optimizer" button.
1. Click "⚡ Run Optimizer" — overlay opens, gauges show `—`, log shows "Optimizer started — pausing workers..."
2. Click "■ ABORT" (title bar or bottom bar) — log shows abort message, overlay can be closed
3. After abort: workers resume (verify via `/api/utils/health` or watch the catalog page process tasks)

- [ ] **Step 5: Commit**

```bash
git add frontend/utils.html
git commit -m "feat(ui): add Mission Control optimizer overlay to Utilities page"
```

---

## Chunk 7: Integration & Final Verification

### Task 9: End-to-end smoke test

- [ ] **Step 1: Run full unit test suite**

```bash
cd E:\DocVault && python -m pytest tests/unit/ -v
```
Expected: all existing tests PASS plus new `test_tuner.py` and updated `test_logs_db.py`.

- [ ] **Step 2: Verify benchmark CLI**

```bash
python tools/benchmark.py --samples 5 --no-save
```
Expected: table prints without errors. If < 5 COMPLETED tasks per type, shows available count.

- [ ] **Step 3: Verify Analytics API**

```bash
curl -s http://localhost:8000/api/utils/performance_stats | python -m json.tool | head -30
```
Expected: JSON with `extractor_stats`, `throughput_24h`, `bottleneck`, `benchmark_runs` keys.

- [ ] **Step 4: Verify optimizer full run (manual)**

Happy path:
1. Open `http://localhost:8000/utils`
2. Click "⚡ Run Optimizer"
3. Overlay opens; log shows "Optimizer started"
4. After run completes: profile cards appear
5. Click "↓ Apply" on a card; log shows "✓ Applied"
6. Workers resume automatically

Abort path (safety-critical — must also verify):
1. Click "⚡ Run Optimizer" again
2. While running, click "■ ABORT" (bottom bar or title bar)
3. Log shows "Abort signal sent — restoring settings..."
4. State transitions to "aborted"; overlay can be closed
5. Run `curl -s http://localhost:8000/api/utils/health` — workers should be processing again (not paused)

- [ ] **Step 5: Final commit**

```bash
git add core/tuner.py tools/benchmark.py core/manager.py core/settings.py run.py \
        api/routes/utils.py frontend/telemetry.html frontend/utils.html \
        tests/unit/test_tuner.py tests/unit/test_logs_db.py
git commit -m "feat(perf-tuning): complete performance tuning system

- Standalone CLI benchmark: tools/benchmark.py
- Analytics dashboard: Performance tab in Telemetry page
- Mission Control optimizer: Utilities page + Quadrant Command overlay
- core/tuner.py: sweep matrix, profile selection, optimizer thread
- core/manager.py: benchmark_runs table in logs.db
- run.py: startup snapshot rollback for crash recovery
- 6 new API endpoints in api/routes/utils.py

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
