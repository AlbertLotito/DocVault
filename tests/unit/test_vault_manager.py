import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def _dbs(tmp_path, monkeypatch):
    import core.manager as m
    db  = str(tmp_path / 'docvault.db')
    sdb = str(tmp_path / 'settings.db')
    monkeypatch.setattr(m, 'get_db_path',          lambda db_path=None: db)
    monkeypatch.setattr(m, 'get_settings_db_path', lambda: sdb)
    m.init_settings_db(); m.init_db(db)
    return db, sdb

def test_create_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager
    vm = VaultManager(db)
    v  = vm.create_vault('Art', 'E:/Art')
    assert v['name'] == 'Art'
    assert v['state'] == 'active'
    assert v['vault_id']

def test_list_vaults(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager
    vm = VaultManager(db)
    vm.create_vault('A', 'E:/A'); vm.create_vault('B', 'E:/B')
    vaults = vm.list_vaults()
    assert len(vaults) == 2

def test_archive_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager
    vm = VaultManager(db)
    v  = vm.create_vault('Test', 'E:/Test')
    vm.transition(v['vault_id'], 'archived')
    updated = vm.get_vault(v['vault_id'])
    assert updated['state'] == 'archived'

def test_cannot_gut_active_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager, VaultStateError
    vm = VaultManager(db)
    v  = vm.create_vault('Test', 'E:/Test')
    try:
        vm.transition(v['vault_id'], 'gutted')
        assert False, "Should have raised"
    except VaultStateError:
        pass

def test_cannot_delete_archived_vault(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager, VaultStateError
    vm = VaultManager(db)
    v  = vm.create_vault('Test', 'E:/Test')
    vm.transition(v['vault_id'], 'archived')
    try:
        vm.transition(v['vault_id'], 'deleted')
        assert False, "Should have raised"
    except VaultStateError:
        pass

def test_duplicate_scan_directory_rejected(tmp_path, monkeypatch):
    db, _ = _dbs(tmp_path, monkeypatch)
    from core.vault_manager import VaultManager, VaultConflictError
    vm = VaultManager(db)
    vm.create_vault('A', 'E:/Shared')
    try:
        vm.create_vault('B', 'E:/Shared')
        assert False, "Should have raised"
    except VaultConflictError:
        pass
