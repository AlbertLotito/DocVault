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

_benchmark_module = None

CHUNK_SIZES    = ['400', '600', '800', '1000']
CHUNK_OVERLAPS = ['50', '100']


def build_sweep_matrix(
    snapshot: dict,
    gpu_util_pct: Optional[float],
    locked_axes: set | None = None,
    force_parallel: bool = False,
) -> list[dict]:
    """
    Build the list of parameter dicts to test.
    First entry always equals the production snapshot (baseline run).
    snapshot keys: embeddings:chunk_size, embeddings:chunk_overlap,
                   ollama:max_parallel, monitor:gpu_temp_throttle
    gpu_util_pct: current GPU utilisation (0-100) or None if no sensor.
    locked_axes: set of axis names to skip sweeping (locked at snapshot value).
                 Valid names: chunk_size, chunk_overlap, max_parallel, gpu_throttle
    force_parallel: if True always include max_parallel=2 regardless of GPU
                    headroom check (only applies if max_parallel axis is not locked).
    Spec: if gpu_util_pct is None (no sensor), max_parallel stays at [1] only
          (unless force_parallel=True).
    """
    if locked_axes is None:
        locked_axes = set()

    current_throttle = float(snapshot.get('monitor:gpu_temp_throttle') or '80')
    relaxed_throttle = str(min(current_throttle + 5, 90.0))

    if 'gpu_throttle' in locked_axes:
        throttle_vals = [snapshot['monitor:gpu_temp_throttle']]
    else:
        throttle_vals = [snapshot['monitor:gpu_temp_throttle']]
        if min(current_throttle + 5, 90.0) != current_throttle:
            throttle_vals.append(relaxed_throttle)

    if 'chunk_size' in locked_axes:
        chunk_sizes = [snapshot['embeddings:chunk_size']]
    else:
        chunk_sizes = CHUNK_SIZES

    if 'chunk_overlap' in locked_axes:
        chunk_overlaps = [snapshot['embeddings:chunk_overlap']]
    else:
        chunk_overlaps = CHUNK_OVERLAPS

    # Only test max_parallel=2 if GPU util headroom > 20% AND sensor is available.
    # Spec: "If no GPU sensor available (gpu_util_pct is None), uses [1] only."
    if 'max_parallel' in locked_axes:
        parallel_vals = [snapshot['ollama:max_parallel']]
    else:
        parallel_vals = [snapshot['ollama:max_parallel']]
        if force_parallel:
            if '2' != snapshot['ollama:max_parallel']:
                parallel_vals.append('2')
        elif gpu_util_pct is not None and gpu_util_pct < 80.0:
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

    for cs in chunk_sizes:
        for co in chunk_overlaps:
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
    'state':               'idle',    # idle | scheduled | starting | running | complete | aborted | error
    'run_index':           0,
    'total_runs':          0,
    'current_params':      {},
    'current_throughput':  None,
    'baseline_throughput': None,
    'runs':                [],
    'hardware':            {},
    'profiles':            None,
    'error':               None,
    'config':              None,
}

# Persists last config across state resets — used by re-run feature
_last_config: dict | None = None


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
    """Read current production values for swept keys.
    Prefers settings.db (direct query, avoids reading back our own snapshot),
    falls back to settings.get() for keys that only exist in config.ini/defaults.
    """
    from core.manager import get_settings_db_path, _connect
    from core.settings import settings as _settings
    result = {}
    with _connect(get_settings_db_path()) as conn:
        for k in _SWEPT_KEYS:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (k,)
            ).fetchone()
            if row:
                result[k] = str(row['value'])
            else:
                # Key lives in config.ini or schema default — read effective value
                result[k] = str(_settings.get(k) or '')
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

def run_mini_benchmark(
    db_path: str,
    n: int,
    vault_id: str | None = None,
    types: list[str] | None = None,
) -> float:
    """
    Run in-process benchmark. Returns overall files/hour (in-process).
    Imports tools/benchmark.py once and caches the module to avoid repeated exec.
    types: list of category strings e.g. ['text','pdf','image']. When None, all.
    """
    global _benchmark_module
    if _benchmark_module is None:
        import importlib.util, os as _os
        spec = importlib.util.spec_from_file_location(
            'benchmark',
            _os.path.join(_os.path.dirname(__file__), '..', 'tools', 'benchmark.py')
        )
        _benchmark_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_benchmark_module)
    bm = _benchmark_module

    if types is None:
        types = list(bm.TYPE_EXTENSIONS.keys())
    results = bm.run_benchmark(db_path, n, types, vault_id)
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

def _run_sweep(
    db_path: str,
    samples: int,
    types: list[str] | None = None,
    locked_axes: set | None = None,
    force_parallel: bool = False,
    vault_id: str | None = None,
):
    """Main optimizer body — runs in daemon thread."""
    from core import manager as _manager

    snapshot = _read_snapshot_keys()
    # IMPORTANT: write snapshot BEFORE pausing workers so crash recovery
    # (_rollback_optimizer_snapshot in run.py) can restore settings even if
    # we never reach set_pause_state. Do not reorder these two lines.
    _write_snapshot(snapshot)

    # Pause workers
    _manager.set_pause_state(True)
    _update_state(state='running')

    try:
        hw = _read_hardware()
        gpu_util = hw.get('gpu_util')
        matrix = build_sweep_matrix(
            snapshot,
            gpu_util_pct=gpu_util,
            locked_axes=locked_axes,
            force_parallel=force_parallel,
        )
        original_throttle = float(snapshot.get('monitor:gpu_temp_throttle') or '80')

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
            throughput = run_mini_benchmark(db_path, samples, vault_id=vault_id, types=types)
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


def start_optimizer(db_path: str, config: dict | None = None) -> bool:
    """
    Start the optimizer. Returns False if already running (caller should 409).
    config keys (all optional):
        samples      int  — files per type (default: setting or 5)
        types        list — categories to benchmark (default: all)
        locked_axes  list — axis names to skip sweeping
        force_parallel bool — always include max_parallel=2
        vault_id     str|None — filter benchmark to this vault
        delay_hours  float — if >0, delay start by this many hours
    """
    global _optimizer_thread, _abort_event, _last_config
    if _optimizer_thread is not None and _optimizer_thread.is_alive():
        return False

    _abort_event = threading.Event()
    cfg = config or {}

    from core.settings import settings
    samples      = int(cfg.get('samples') or settings.get('tuning:benchmark_samples_per_type') or 5)
    types        = cfg.get('types') or None
    locked_axes  = set(cfg.get('locked_axes') or [])
    force_parallel = bool(cfg.get('force_parallel', False))
    vault_id     = cfg.get('vault_id') or None
    delay_hours  = float(cfg.get('delay_hours') or 0)

    # Persist resolved config for re-run
    resolved_config = {
        'samples':       samples,
        'types':         types,
        'locked_axes':   list(locked_axes),
        'force_parallel': force_parallel,
        'vault_id':      vault_id,
        'delay_hours':   0,  # re-run never delays again
    }
    _last_config = resolved_config

    _update_state(
        state='starting', run_index=0, total_runs=0,
        current_params={}, current_throughput=None,
        baseline_throughput=None, runs=[], profiles=None, error=None,
        config=resolved_config,
    )

    sweep_kwargs = dict(
        db_path=db_path,
        samples=samples,
        types=types,
        locked_axes=locked_axes,
        force_parallel=force_parallel,
        vault_id=vault_id,
    )

    if delay_hours > 0:
        _update_state(state='scheduled')

        def _delayed():
            _update_state(state='starting')
            _run_sweep(**sweep_kwargs)

        t = threading.Timer(delay_hours * 3600, _delayed)
        t.daemon = True
        t.start()
        _optimizer_thread = t  # Timer is not a Thread but has is_alive()
    else:
        _optimizer_thread = threading.Thread(
            target=_run_sweep, kwargs=sweep_kwargs, daemon=True, name='optimizer'
        )
        _optimizer_thread.start()

    return True


def get_last_config() -> dict | None:
    """Return the config dict from the last sweep (None if none has ever run)."""
    return _last_config


def abort_optimizer() -> bool:
    """Signal abort. Returns False if no active run."""
    if _optimizer_thread is None or not _optimizer_thread.is_alive():
        return False
    _abort_event.set()
    return True
