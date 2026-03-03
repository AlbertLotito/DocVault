import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _setup(tmp_path, monkeypatch):
    import core.manager as m
    db  = str(tmp_path / 'docvault.db')
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_db_path',          lambda db_path=None: db)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db()
    m.init_db(db)
    return m, db


def test_bootstrap_creates_documents_vault(tmp_path, monkeypatch):
    m, db = _setup(tmp_path, monkeypatch)
    m.bootstrap_default_vault(db, scan_directory='E:/test/docs')
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT name, scan_directory, state FROM vaults").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 'Documents'
    assert row[1] == 'E:/test/docs'
    assert row[2] == 'active'


def test_bootstrap_assigns_existing_tasks(tmp_path, monkeypatch):
    m, db = _setup(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO tasks (file_hash, file_path, file_type) VALUES ('abc123', '/a/b.pdf', 'pdf')"
    )
    conn.commit()
    conn.close()
    m.bootstrap_default_vault(db, scan_directory='E:/test/docs')
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT vault_id FROM tasks WHERE file_hash='abc123'").fetchone()
    conn.close()
    assert row[0] is not None


def test_bootstrap_is_idempotent(tmp_path, monkeypatch):
    m, db = _setup(tmp_path, monkeypatch)
    m.bootstrap_default_vault(db, 'E:/test/docs')
    m.bootstrap_default_vault(db, 'E:/test/docs')  # second call must not raise or duplicate
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
    conn.close()
    assert count == 1


def test_bootstrap_skips_if_vault_exists(tmp_path, monkeypatch):
    m, db = _setup(tmp_path, monkeypatch)
    m.bootstrap_default_vault(db, 'E:/first')
    m.bootstrap_default_vault(db, 'E:/second')  # should NOT create a second vault
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM vaults").fetchone()[0]
    conn.close()
    assert count == 1
