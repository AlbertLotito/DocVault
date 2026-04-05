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


def _make_scan_dir(tmp_path, structure):
    """
    Create a directory structure from a dict.
    Keys are paths relative to tmp_path; values are file content bytes.
    Intermediate directories are created automatically.
    Example: {'docs/file.txt': b'hello', 'audio_data/song.au': b'audio'}
    """
    for rel_path, content in structure.items():
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
    return str(tmp_path)


def test_global_extension_ignore(db, tmp_path):
    """ingest() with global ignore_extensions='.au' skips .au files."""
    scan_dir = _make_scan_dir(tmp_path, {
        'doc.pdf': b'pdf content',
        'noise.au': b'audio block',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '2024-01-01', '2024-01-01')""", (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: {
        'ingestion:ignore_extensions': '.au',
        'ingestion:ignore_folders': '',
    }.get(key, '')

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert any('doc.pdf' in p for p in paths), "pdf should be indexed"
    assert not any('noise.au' in p for p in paths), ".au file should be ignored"


def test_global_folder_ignore(db, tmp_path):
    """ingest() with global ignore_folders='*_data' skips entire subtree."""
    scan_dir = _make_scan_dir(tmp_path, {
        'report.txt': b'report',
        'audio_data/block1.au': b'block1',
        'audio_data/block2.au': b'block2',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '2024-01-01', '2024-01-01')""", (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: {
        'ingestion:ignore_extensions': '',
        'ingestion:ignore_folders': '*_data',
    }.get(key, '')

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert any('report.txt' in p for p in paths), "report should be indexed"
    assert not any('audio_data' in p for p in paths), "audio_data subtree should be ignored entirely"


def test_vault_extension_ignore(two_vault_db, tmp_path):
    """Per-vault .au ignore skips .au for vault-a; vault-b still indexes them."""
    scan_dir = _make_scan_dir(tmp_path, {
        'song.au': b'audio data',
        'doc.txt': b'text',
    })

    mock_settings = MagicMock()
    mock_settings.get = lambda key: ''  # no global ignores

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        with _connect(two_vault_db) as conn:
            vault_a = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='vault-a'").fetchone())
            vault_b = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='vault-b'").fetchone())
        ingestor.ingest(scan_dir, two_vault_db, vault_id='vault-a', vault_row=vault_a)
        ingestor.ingest(scan_dir, two_vault_db, vault_id='vault-b', vault_row=vault_b)

    with _connect(two_vault_db) as conn:
        fv_a = [r['file_path'] for r in
                conn.execute("SELECT file_path FROM file_vault WHERE vault_id='vault-a'").fetchall()]
        fv_b = [r['file_path'] for r in
                conn.execute("SELECT file_path FROM file_vault WHERE vault_id='vault-b'").fetchall()]

    assert not any('song.au' in p for p in fv_a), "vault-a should skip .au"
    assert any('song.au' in p for p in fv_b), "vault-b should index .au (no ignore rule)"


def test_vault_folder_fnmatch(db, tmp_path):
    """temp* matches 'temporary' and 'temp1' but NOT 'atemp'."""
    scan_dir = _make_scan_dir(tmp_path, {
        'temporary/file.txt': b'in temporary',
        'temp1/file.txt': b'in temp1',
        'atemp/file.txt': b'in atemp - should be indexed',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state,
                        ignore_extensions, ignore_folders, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '', 'temp*', '2024-01-01', '2024-01-01')""",
                     (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: ''

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert not any('temporary' in p for p in paths), "temporary/ should be pruned"
    assert not any('temp1' in p for p in paths), "temp1/ should be pruned"
    assert any('atemp' in p for p in paths), "atemp/ should NOT be pruned (doesn't start with temp)"


def test_effective_rules_are_union(db, tmp_path):
    """Global has .bak, vault has .tmp — both are ignored (union, not override)."""
    scan_dir = _make_scan_dir(tmp_path, {
        'keep.txt': b'keep',
        'noise.bak': b'bak',
        'noise.tmp': b'tmp',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state,
                        ignore_extensions, ignore_folders, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '.tmp', '', '2024-01-01', '2024-01-01')""",
                     (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: {
        'ingestion:ignore_extensions': '.bak',
        'ingestion:ignore_folders': '',
    }.get(key, '')

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert any('keep.txt' in p for p in paths)
    assert not any('noise.bak' in p for p in paths), ".bak from global rules should be ignored"
    assert not any('noise.tmp' in p for p in paths), ".tmp from vault rules should be ignored"
