import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def test_vaults_table_exists(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    m.init_db(db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert 'vaults' in tables


def test_vaults_table_has_required_columns(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    m.init_db(db)
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()}
    conn.close()
    assert {'vault_id', 'name', 'scan_directory', 'priority', 'color', 'state',
            'created_at', 'updated_at'}.issubset(cols)


def test_tasks_has_vault_id_column(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    m.init_db(db)
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    conn.close()
    assert 'vault_id' in cols


def test_vault_settings_table_exists(tmp_path, monkeypatch):
    import core.manager as m
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db()
    conn = sqlite3.connect(sdb)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert 'vault_settings' in tables
