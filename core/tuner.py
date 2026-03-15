"""
core/tuner.py — Performance optimizer for DocVault.

Owns: sweep matrix generation, profile selection.
The optimizer thread and state management are added in Task 5.
"""
import json
import threading
import time
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
    Spec: if gpu_util_pct is None (no sensor), max_parallel stays at [1] only.
    """
    current_throttle = float(snapshot.get('monitor:gpu_temp_throttle', '80'))
    relaxed_throttle = str(min(current_throttle + 5, 90.0))
    throttle_vals = [snapshot['monitor:gpu_temp_throttle']]
    if min(current_throttle + 5, 90.0) != current_throttle:
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
    Each profile includes a 'label' field for the frontend.
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


# ── Module-level optimizer state ──────────────────────────────────────────────

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
    """Read current production values for swept keys via direct DB query."""
    from core.manager import get_settings_db_path, _connect
    result = {}
    with _connect(get_settings_db_path()) as conn:
        for k in _SWEPT_KEYS:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (k,)
            ).fetchone()
            result[k] = str(row['value']) if row else ''
    return result


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
    # Use same weighted logic as benchmark's save_result
    pending = bm._get_pending_counts(db_path, types)
    by_type = results['by_type']
    weighted_sum = 0.0
    weight_total = 0
    for cat in types:
        r = by_type.get(cat, {})
        tp = r.get('throughput', 0)
        pend = pending.get(cat, 0)
        if tp > 0 and pend > 0:
            weighted_sum += tp * pend
            weight_total += pend
    if weight_total > 0:
        return weighted_sum / weight_total
    throughputs = [r['throughput'] for r in by_type.values() if r.get('throughput', 0) > 0]
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
