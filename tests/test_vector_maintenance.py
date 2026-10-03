"""
Vector store upkeep: keep the LanceDB table compacted and indexed.

Every embedding batch appends a small fragment and nothing ever compacted them:
the live table reached 52,163 fragments (median 5 rows) and 12,000 versions, and
with no index on the merge key `id`, each upsert scanned all of it (one batch
stalled 20+ minutes). Compaction brought it to 3 fragments; these tests keep it
that way.
"""
import uuid
from unittest.mock import MagicMock, patch

from embeddings.vector_store import VectorStore
from workers import embedding_worker


def _store():
    return VectorStore(collection=f'test_{uuid.uuid4().hex[:8]}')


def _chunk(i):
    return {'chunk_index': i, 'vector': [float(i % 7)] * 768,
            'payload': {'file_path': f'/f{i}.txt', 'chunk_text': f'text {i}', 'chunk_index': i}}


def _index_columns(vs):
    return {tuple(ix.columns) for ix in vs._table.list_indices()}


def test_new_table_gets_scalar_indexes_on_merge_and_delete_keys():
    vs = _store()
    vs.upsert_documents({'h0': [_chunk(0)]})
    vs.ensure_scalar_indexes()
    assert {('id',), ('file_hash',)} <= _index_columns(vs)


def test_optimize_compacts_fragments_and_keeps_every_row():
    vs = _store()
    for i in range(12):                       # each upsert appends its own fragment
        vs.upsert_documents({f'h{i}': [_chunk(i)]})
    before = vs._table.stats()['fragment_stats']['num_fragments']

    vs.optimize()

    assert before >= 12
    assert vs._table.stats()['fragment_stats']['num_fragments'] == 1
    assert vs.count() == 12


# ── Embedding worker schedule ────────────────────────────────────────────────

def _settings(hours):
    return MagicMock(get=lambda key: {'lancedb:optimize_interval_hours': hours}.get(key))


def test_worker_optimizes_once_the_interval_has_passed(monkeypatch):
    vs = MagicMock()
    monkeypatch.setattr(embedding_worker, '_last_optimize', 0.0)
    with patch.object(embedding_worker, 'settings', _settings(6)), \
         patch.object(embedding_worker.time, 'monotonic', return_value=6 * 3600 + 1):
        embedding_worker._maybe_optimize(vs)
    vs.optimize.assert_called_once()
    vs.ensure_scalar_indexes.assert_called_once()


def test_worker_does_not_optimize_again_within_the_interval(monkeypatch):
    vs = MagicMock()
    monkeypatch.setattr(embedding_worker, '_last_optimize', 1000.0)
    with patch.object(embedding_worker, 'settings', _settings(6)), \
         patch.object(embedding_worker.time, 'monotonic', return_value=1000.0 + 3600):
        embedding_worker._maybe_optimize(vs)
    vs.optimize.assert_not_called()


def test_interval_zero_disables_optimization(monkeypatch):
    vs = MagicMock()
    monkeypatch.setattr(embedding_worker, '_last_optimize', 0.0)
    with patch.object(embedding_worker, 'settings', _settings(0)), \
         patch.object(embedding_worker.time, 'monotonic', return_value=10 ** 9):
        embedding_worker._maybe_optimize(vs)
    vs.optimize.assert_not_called()


def test_optimize_failure_is_logged_not_raised(monkeypatch):
    vs = MagicMock()
    vs.optimize.side_effect = RuntimeError('lance boom')
    monkeypatch.setattr(embedding_worker, '_last_optimize', 0.0)
    with patch.object(embedding_worker, 'settings', _settings(6)), \
         patch.object(embedding_worker.time, 'monotonic', return_value=10 ** 9):
        embedding_worker._maybe_optimize(vs)      # must not raise
    # and it doesn't retry in a tight loop: the clock was still advanced
    assert embedding_worker._last_optimize == 10 ** 9


def test_optimize_interval_setting_exists():
    from core.settings import SETTINGS_SCHEMA
    s = SETTINGS_SCHEMA['lancedb:optimize_interval_hours']
    assert s['type'] == 'int' and s['default'] == 6 and s['group'] == 'lancedb'
