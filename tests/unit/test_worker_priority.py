import sqlite3, time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _db(tmp_path, monkeypatch):
    import core.manager as m
    db = str(tmp_path / 'docvault.db')
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_db_path', lambda db_path=None: db)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db(); m.init_db(db)
    m.bootstrap_default_vault(db, 'E:/test')
    return m, db

def test_high_vault_priority_claimed_first(tmp_path, monkeypatch):
    m, db = _db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    # Insert two vaults
    conn.execute("INSERT INTO vaults (vault_id,name,scan_directory,priority,color,state,created_at,updated_at) VALUES ('v-high','High','E:/h',1,'#fff','active',NULL,NULL)")
    conn.execute("INSERT INTO vaults (vault_id,name,scan_directory,priority,color,state,created_at,updated_at) VALUES ('v-low', 'Low', 'E:/l',9,'#000','active',NULL,NULL)")
    # Two tasks, same extractor priority
    conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                 "VALUES ('h1','/h/f.pdf','pdf','PENDING',10,'v-high')")
    conn.execute("INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id) "
                 "VALUES ('l1','/l/f.pdf','pdf','PENDING',10,'v-low')")
    conn.commit(); conn.close()

    task = m.claim_pending_task(db, 'worker-1')
    assert task is not None
    assert task['vault_id'] == 'v-high'

def test_aging_raises_old_low_priority_task(tmp_path, monkeypatch):
    """A very old task should have a better effective priority than a new one."""
    m, db = _db(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO vaults (vault_id,name,scan_directory,priority,color,state,created_at,updated_at) VALUES ('v1','V1','E:/v1',5,'#fff','active',NULL,NULL)")
    # Old task: created 2 hours ago, low vault priority
    conn.execute("""INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id,last_update)
                    VALUES ('old1','/old.pdf','pdf','PENDING',5,'v1',
                    datetime('now','-7200 seconds'))""")
    # New task: created now, same vault priority
    conn.execute("""INSERT INTO tasks (file_hash,file_path,file_type,status,priority,vault_id,last_update)
                    VALUES ('new1','/new.pdf','pdf','PENDING',5,'v1',
                    datetime('now'))""")
    conn.commit(); conn.close()

    task = m.claim_pending_task(db, 'worker-1')
    # The old task should be claimed first due to aging bonus
    assert task['file_hash'] == 'old1'
