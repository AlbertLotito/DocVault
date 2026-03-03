import sqlite3, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _make_settings_db(tmp_path, global_overrides=None, vault_overrides=None):
    db = str(tmp_path / 'settings.db')
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("""CREATE TABLE vault_settings
                    (vault_id TEXT, key TEXT, value TEXT, PRIMARY KEY(vault_id,key))""")
    for k, v in (global_overrides or {}).items():
        conn.execute("INSERT INTO settings VALUES (?,?)", (k, v))
    for (vid, k), v in (vault_overrides or {}).items():
        conn.execute("INSERT INTO vault_settings VALUES (?,?,?)", (vid, k, v))
    conn.commit(); conn.close()
    return db

def test_schema_default_when_no_overrides(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(tmp_path)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id=None)
    assert r.get('embeddings:chunk_size') == 600

def test_global_db_overrides_default(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(tmp_path, global_overrides={'embeddings:chunk_size': '300'})
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id=None)
    assert r.get('embeddings:chunk_size') == '300'

def test_vault_setting_overrides_global(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(
        tmp_path,
        global_overrides={'embeddings:chunk_size': '300'},
        vault_overrides={('vault-1', 'embeddings:chunk_size'): '150'}
    )
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id='vault-1')
    assert r.get('embeddings:chunk_size') == '150'

def test_vault_setting_does_not_leak(tmp_path, monkeypatch):
    import core.manager as m
    db = _make_settings_db(
        tmp_path,
        vault_overrides={('vault-1', 'embeddings:chunk_size'): '150'}
    )
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: db)
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id='vault-2')  # different vault
    assert r.get('embeddings:chunk_size') == 600  # schema default
