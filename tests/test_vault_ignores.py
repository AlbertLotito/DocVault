import os
import pytest
from unittest.mock import patch, MagicMock
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def two_vault_db(tmp_path):
    """DB with vault-a (ignores .au) and vault-b (no ignores)."""
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults
            (vault_id, name, scan_directory, priority, state, ignore_extensions, ignore_folders, created_at, updated_at)
            VALUES ('vault-a', 'Vault A', '/docs', 5, 'active', '.au', '', '2024-01-01', '2024-01-01')""")
        conn.execute("""INSERT INTO vaults
            (vault_id, name, scan_directory, priority, state, ignore_extensions, ignore_folders, created_at, updated_at)
            VALUES ('vault-b', 'Vault B', '/nas', 5, 'active', '', '', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


def test_schema_columns_exist(db):
    """init_db() adds ignore_extensions and ignore_folders columns with empty string defaults."""
    with _connect(db) as conn:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()]
    assert 'ignore_extensions' in cols
    assert 'ignore_folders' in cols

    # New rows get empty string defaults
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
        row = conn.execute("SELECT ignore_extensions, ignore_folders FROM vaults WHERE vault_id='v1'").fetchone()
    assert row['ignore_extensions'] == ''
    assert row['ignore_folders'] == ''
