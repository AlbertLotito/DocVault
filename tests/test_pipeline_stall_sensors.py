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
