import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def test_get_logs_db_path_returns_string(tmp_path, monkeypatch):
    import core.manager as m
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: str(tmp_path / 'docvault.db'))
    path = m.get_logs_db_path()
    assert isinstance(path, str)
    assert path.endswith('logs.db')
    assert os.path.dirname(path) == str(tmp_path)


def test_init_logs_db_creates_all_tables(tmp_path, monkeypatch):
    """init_logs_db() must create all five tables."""
    import core.manager as m
    db = str(tmp_path / 'logs.db')
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: db)
    m.init_logs_db()
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert {'task_timings', 'worker_errors', 'worker_log',
            'extractor_stats', 'system_stats'}.issubset(tables)


def test_init_logs_db_is_idempotent(tmp_path, monkeypatch):
    """Calling init_logs_db() twice must not raise."""
    import core.manager as m
    db = str(tmp_path / 'logs.db')
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: db)
    m.init_logs_db()
    m.init_logs_db()  # second call
