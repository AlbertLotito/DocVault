"""
core/tuner.py — Performance optimizer for DocVault.

Owns: sweep matrix generation, profile selection.
The optimizer thread and state management are added in Task 5.
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
    Spec: if gpu_util_pct is None (no sensor), max_parallel stays at [1] only.
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
