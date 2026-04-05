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
