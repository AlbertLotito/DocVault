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


def test_sweep_matrix_no_gpu_sensor_uses_parallel_1_only():
    """Spec: 'If no GPU sensor available (gpu_util_pct is None), uses [1] only.'"""
    snapshot = {'embeddings:chunk_size': '600', 'embeddings:chunk_overlap': '100',
                'ollama:max_parallel': '1', 'monitor:gpu_temp_throttle': '80'}
    matrix = build_sweep_matrix(snapshot, gpu_util_pct=None)
    parallel_vals = {r['ollama:max_parallel'] for r in matrix}
    assert parallel_vals == {'1'}, "No GPU sensor: must not test max_parallel=2"


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


def test_select_profiles_label_field_present():
    """Each profile must include a 'label' field for the frontend."""
    runs = _make_runs([(847, 71), (1012, 73), (1247, 78)])
    profiles = select_profiles(runs, original_throttle=80.0)
    assert profiles['raw_speed']['label'] == 'Raw Speed'
    assert profiles['sustainable']['label'] == 'Sustainable'
    assert profiles['balanced']['label'] == 'Balanced'
