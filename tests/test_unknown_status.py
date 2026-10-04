"""
Files whose type has no extractor are UNKNOWN (not ERROR), and get re-queued
automatically once a kernel for their type is activated.

The original check compared the router's result with the fallback_kernel
*module*, but the router returns a LazyPythonKernel proxy, so it never matched
and 63,767 unroutable files were reported as ERROR. These tests use the
production shape: a proxy object in router.FALLBACK_KERNEL.
"""
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from core import manager, router
from workers import extraction_worker

_REAL_REQUEUE_HOOK = router._requeue_unknown   # captured before conftest stubs it


@pytest.fixture
def restore_router(monkeypatch):
    """reload() assigns router globals directly; put them back afterwards."""
    for name in ('ROUTES', 'FALLBACK_KERNEL', '_all_extractors', '_initialized'):
        monkeypatch.setattr(router, name, getattr(router, name))
    monkeypatch.setattr(router, '_requeue_unknown', _REAL_REQUEUE_HOOK)


@pytest.fixture
def routed(monkeypatch):
    """Router state as reload() leaves it: 'txt' routed, a proxy fallback for everything else."""
    txt_kernel = MagicMock(name='plaintext proxy')
    txt_kernel.__name__ = 'plaintext_extractor'
    txt_kernel.extract.return_value = ('hello', None)
    fallback = MagicMock(name='fallback proxy')
    fallback.__name__ = 'fallback_kernel'
    monkeypatch.setattr(router, 'ROUTES', {'file': {'txt': [txt_kernel]}, 'folder': {}})
    monkeypatch.setattr(router, 'FALLBACK_KERNEL', fallback)
    monkeypatch.setattr(router, '_initialized', True)
    return txt_kernel, fallback


@patch('workers.extraction_worker.manager.complete_extraction')
def test_unroutable_file_is_unknown_and_fallback_never_runs(mock_complete, routed):
    _, fallback = routed
    extraction_worker.process_task('test.db', {'file_hash': 'h', 'file_path': '/a/thing.xyz', 'file_type': 'xyz'})

    kwargs = mock_complete.call_args[1]
    assert kwargs['status'] == 'UNKNOWN'
    assert kwargs['error'] == 'No extractor for .xyz files'
    fallback.extract.assert_not_called()


@patch('workers.extraction_worker.manager.complete_extraction')
def test_extensionless_file_is_unknown(mock_complete, routed):
    extraction_worker.process_task('test.db', {'file_hash': 'h', 'file_path': '/a/README', 'file_type': ''})
    kwargs = mock_complete.call_args[1]
    assert kwargs['status'] == 'UNKNOWN'
    assert kwargs['error'] == 'No extractor for files without an extension'


@patch('workers.extraction_worker.manager.complete_extraction')
def test_routed_file_still_extracts(mock_complete, routed):
    extraction_worker.process_task('test.db', {'file_hash': 'h', 'file_path': '/a/n.txt', 'file_type': 'txt'})
    assert mock_complete.call_args[1]['status'] == 'EXTRACTED'


# ── Re-queue and migration ───────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'test.db')
    manager.init_db(path)
    return path


def _task(db, h, ftype, status, error=None):
    manager.insert_task(db, h, f'/f/{h}.{ftype}', ftype)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE tasks SET status=?, error_log=? WHERE file_hash=?", (status, error, h))


def _status(db, h):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT status, error_log FROM tasks WHERE file_hash=?", (h,)).fetchone()


def test_requeue_unknown_resets_only_newly_routable_types(db):
    _task(db, 'e1', 'eml', 'UNKNOWN', 'No extractor for .eml files')
    _task(db, 'e2', 'EML', 'UNKNOWN', 'No extractor for .EML files')
    _task(db, 'x1', 'xyz', 'UNKNOWN', 'No extractor for .xyz files')
    _task(db, 'r1', 'eml', 'ERROR', 'real failure')

    n = manager.requeue_unknown(db, {'eml', 'txt'})

    assert n == 2
    assert _status(db, 'e1') == ('PENDING', None)
    assert _status(db, 'e2') == ('PENDING', None)
    assert _status(db, 'x1')[0] == 'UNKNOWN'
    assert _status(db, 'r1')[0] == 'ERROR'


def test_requeue_unknown_with_no_routes_is_a_noop(db):
    _task(db, 'x1', 'xyz', 'UNKNOWN')
    assert manager.requeue_unknown(db, set()) == 0


def test_reload_requeues_unknown_for_routed_types(monkeypatch, restore_router):
    calls = []
    entry = {'module_name': 'email_extractor', 'kernel_type': 'python', 'target_type': 'file',
             'extensions': '["eml", "wdseml"]', 'kernel_id': 'k', 'description': ''}
    rm = MagicMock()
    rm.get_active_kernels.return_value = [entry]
    monkeypatch.setattr(router, 'RegistryManager', lambda: rm)
    monkeypatch.setattr(router.manager, 'requeue_unknown', lambda db, exts: calls.append(set(exts)) or 0)

    router.reload(sync_disk=False)

    assert calls == [{'eml', 'wdseml'}]


def test_reload_survives_a_requeue_failure(monkeypatch, restore_router):
    rm = MagicMock()
    rm.get_active_kernels.return_value = []
    monkeypatch.setattr(router, 'RegistryManager', lambda: rm)
    def boom(db, exts):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(router.manager, 'requeue_unknown', boom)
    router.reload(sync_disk=False)      # must not raise
    assert router._initialized


def test_init_db_converts_fallback_errors_to_unknown(db):
    _task(db, 'u1', 'h', 'ERROR', '[fallback_kernel] Unsupported format: H')
    _task(db, 'r1', 'pdf', 'ERROR', '[text_extractor] Corrupt PDF')

    manager.init_db(db)

    assert _status(db, 'u1') == ('UNKNOWN', 'No extractor for .h files')
    assert _status(db, 'r1')[0] == 'ERROR'


def test_opus_voice_notes_are_routed_to_transcription_and_diagnostics():
    """WhatsApp voice notes (.opus) sat UNKNOWN: no kernel claimed the extension."""
    import ast, glob, os
    ext_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'extractors')
    claims = {}
    for path in glob.glob(os.path.join(ext_dir, '*.py')):
        for node in ast.parse(open(path, encoding='utf-8').read()).body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'MANIFEST' for t in node.targets):
                for ext in ast.literal_eval(node.value).get('extensions', []):
                    claims.setdefault(ext, set()).add(os.path.basename(path)[:-3])
    assert claims.get('opus') == {'aural_intelligence_extractor', 'media_technical_diagnostics_extractor'}
