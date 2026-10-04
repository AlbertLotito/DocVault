"""Parallel extraction workers (workers:extract_concurrency).

Measured 2026-10-04: the single extraction worker was busy 51 of 60 minutes
while CPU sat at 14% (16 cores), RAM 29%, GPU 16%. Parallel workers need:
- lazy kernel loading to happen exactly once (two workers loading Whisper
  large-v3 at once would put a second ~10 GB copy on the GPU);
- non-thread-safe kernels (shared Whisper model, OpenCV/FER face models, Word
  COM) to take turns, while everything else runs in parallel.
"""
import threading
import time
from unittest.mock import MagicMock, patch

from core import router
from workers import extraction_worker


def test_lazy_kernel_loads_once_under_concurrent_access(tmp_path, monkeypatch):
    marker = tmp_path / 'loads.txt'
    mod = tmp_path / 'slow_kernel.py'
    # Each execution of the module body appends to a shared file: a per-module
    # counter can't see duplicates, since each load is a fresh module object.
    mod.write_text(f'import time\nopen({str(marker)!r}, "a").write("x")\ntime.sleep(0.3)\n'
                   'def extract(p, c):\n    return "ok", None\n', encoding='utf-8')
    monkeypatch.setattr(router, 'EXTRACTORS_DIR', str(tmp_path))
    k = router.LazyPythonKernel('slow_kernel')
    results = []
    threads = [threading.Thread(target=lambda: results.append(k.extract)) for _ in range(6)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(results) == 6
    assert marker.read_text() == 'x'                    # module body executed exactly once


def _concurrency_of(kernel_name, workers=4):
    """Run process_task on several threads with one kernel; report the peak
    number of simultaneous extract() calls."""
    active, peak, lock = [0], [0], threading.Lock()

    def extract(path, ctx):
        with lock:
            active[0] += 1; peak[0] = max(peak[0], active[0])
        time.sleep(0.2)
        with lock:
            active[0] -= 1
        return 'text', None

    k = MagicMock(); k.__name__ = kernel_name; k.extract.side_effect = extract
    with patch('workers.extraction_worker.router.get_extractors', return_value=[k]), \
         patch('workers.extraction_worker.manager.complete_extraction'):
        ts = [threading.Thread(target=extraction_worker.process_task,
                               args=('t.db', {'file_hash': f'h{i}', 'file_path': f'/f{i}.x', 'file_type': 'x'}))
              for i in range(workers)]
        for t in ts: t.start()
        for t in ts: t.join()
    return peak[0]


def test_thread_safe_kernels_run_in_parallel():
    assert _concurrency_of('text_extractor') > 1


def test_whisper_word_and_face_kernels_take_turns():
    for name in ('aural_intelligence_extractor', 'multimodal_video_intelligence_extractor',
                 'microsoft_word_extractor', 'face_identity_extractor', 'face_analytics_extractor'):
        assert _concurrency_of(name) == 1, name


def test_run_py_builds_n_extraction_workers_with_distinct_ids():
    import run
    entries = run._make_extract_workers('db.sqlite', 3)
    assert [name for name, _, _ in entries] == ['extraction-0', 'extraction-1', 'extraction-2']
    ids = [args[1] for _, _, args in entries]
    assert len(set(ids)) == 3 and all(i.startswith('extract-') for i in ids)
    assert all(fn is extraction_worker.run for _, fn, _ in entries)


def test_extract_concurrency_setting_exists():
    from core.settings import SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['workers:extract_concurrency']
    assert s['type'] == 'int' and s['default'] == 1
