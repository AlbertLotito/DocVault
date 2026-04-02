import sqlite3, tempfile, os, pytest

def make_logs_db():
    """Create an in-memory logs.db and run init_logs_db() against it."""
    import unittest.mock as mock
    tmp = tempfile.mktemp(suffix='.db')
    with mock.patch('core.manager.get_logs_db_path', return_value=tmp):
        from core.manager import init_logs_db
        init_logs_db()
    return tmp

def test_system_stats_has_queue_columns():
    db = make_logs_db()
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(system_stats)').fetchall()]
    conn.close()
    os.unlink(db)
    assert 'extracted_queue' in cols
    assert 'embedding_queue' in cols


from unittest.mock import patch, MagicMock

def test_process_task_batch_records_timing():
    """After a successful batch, _record_timing is called once per doc with extractor='embedding'."""
    import workers.embedding_worker as ew

    task = {
        'file_hash': 'abc123',
        'file_path': '/fake/doc.txt',
        'extracted_text': 'hello world ' * 100,
        'vault_id': None,
        'file_size': 100,
    }

    mock_vs = MagicMock()
    mock_vs.upsert_batch.return_value = None

    recorded = []

    def fake_record_timing(t, extractor, elapsed):
        recorded.append((t['file_hash'], extractor, elapsed))

    with patch('workers.embedding_worker.embedder.embed_batch', return_value=[[0.1]*384]):
        with patch('workers.embedding_worker.manager.update_task_status'):
            with patch('workers.embedding_worker.manager.update_task_progress'):
                with patch('workers.embedding_worker._record_timing', side_effect=fake_record_timing):
                    ew.process_task_batch('/fake/db', [task], mock_vs)

    assert len(recorded) == 1
    assert recorded[0][0] == 'abc123'
    assert recorded[0][1] == 'embedding'
    assert recorded[0][2] > 0
