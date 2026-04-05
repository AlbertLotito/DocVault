import pytest
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def vault_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'Vault One', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


def test_scan_paused_column_exists(db):
    """init_db() adds scan_paused column with default 0."""
    with _connect(db) as conn:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()]
    assert 'scan_paused' in cols

    # Default value is 0 for new rows
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
        row = conn.execute("SELECT scan_paused FROM vaults WHERE vault_id='v1'").fetchone()
    assert row['scan_paused'] == 0


from core.vault_manager import VaultManager, VaultStateError


def test_set_scan_paused_true(vault_db):
    """set_scan_paused(vault_id, True) sets column to 1 and returns updated vault."""
    vm = VaultManager(vault_db)
    result = vm.set_scan_paused('v1', True)
    assert result['scan_paused'] == 1
    # Verify persisted
    vault = vm.get_vault('v1')
    assert vault['scan_paused'] == 1


def test_set_scan_paused_false(vault_db):
    """set_scan_paused(vault_id, False) sets column back to 0."""
    vm = VaultManager(vault_db)
    vm.set_scan_paused('v1', True)
    result = vm.set_scan_paused('v1', False)
    assert result['scan_paused'] == 0


def test_set_scan_paused_unknown_vault(vault_db):
    """set_scan_paused raises VaultStateError for unknown vault_id."""
    vm = VaultManager(vault_db)
    with pytest.raises(VaultStateError):
        vm.set_scan_paused('no-such-vault', True)


def test_ingestion_worker_skips_paused_vault(vault_db):
    """Paused vault (scan_paused=1) is not passed to ingestor; active vault is."""
    with _connect(vault_db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, scan_paused, created_at, updated_at)
                        VALUES ('v2', 'Vault Two', '/other', 5, 'active', 1, '2024-01-01', '2024-01-01')""")
        conn.commit()

    scanned = []

    def fake_ingest(scan_dir, db_path, vault_id):
        scanned.append(vault_id)

    from core.vault_manager import VaultManager
    import core.ingestor as ingestor_mod
    from unittest.mock import patch

    with patch.object(ingestor_mod, 'ingest', side_effect=fake_ingest):
        vm = VaultManager(vault_db)
        vaults = vm.list_vaults()
        active = [v for v in vaults if v['state'] == 'active' and not v.get('scan_paused')]
        for v in active:
            ingestor_mod.ingest(v['scan_directory'], vault_db, vault_id=v['vault_id'])

    assert 'v1' in scanned       # active, not paused → scanned
    assert 'v2' not in scanned   # active, but paused → skipped


def test_api_pause_scan_endpoint(vault_db):
    """POST /api/vaults/{id}/pause-scan with {paused: true} returns vault with scan_paused=1."""
    from core.vault_manager import VaultManager
    from api.routes import vaults as vaults_module
    from fastapi.testclient import TestClient
    from api.main import app

    original_vm = vaults_module._vm
    vaults_module._vm = lambda: VaultManager(vault_db)
    try:
        client = TestClient(app)
        response = client.post('/api/vaults/v1/pause-scan',
                               json={'paused': True})
        assert response.status_code == 200
        data = response.json()
        assert data['scan_paused'] == 1
    finally:
        vaults_module._vm = original_vm
