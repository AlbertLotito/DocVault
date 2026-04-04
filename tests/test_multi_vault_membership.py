import pytest
import os
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
                        VALUES ('vault-a', 'Vault A', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-b', 'Vault B', '//nas/photos', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


# ── Test 1 ─────────────────────────────────────────────────────────────────────

def test_file_vault_table_created(db):
    with _connect(db) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
    assert 'file_vault' in tables


# ── Test 2 ─────────────────────────────────────────────────────────────────────

def test_backfill_on_migration(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('abc', '/docs/a.pdf', 'pdf', 'v1')")
        conn.commit()
    manager.init_db(db_path)  # re-run triggers backfill
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM file_vault WHERE file_hash='abc'").fetchone()
    assert row is not None
    assert row['vault_id'] == 'v1'
    assert row['file_path'] == '/docs/a.pdf'


# ── Test 3 ─────────────────────────────────────────────────────────────────────

def test_upsert_file_vault_new(vault_db):
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.commit()
    manager.upsert_file_vault(vault_db, 'h1', 'vault-a', '/docs/a.txt')
    with _connect(vault_db) as conn:
        row = conn.execute("SELECT * FROM file_vault WHERE file_hash='h1' AND vault_id='vault-a'").fetchone()
    assert row is not None
    assert row['file_path'] == os.path.normpath('/docs/a.txt')


# ── Test 4 ─────────────────────────────────────────────────────────────────────

def test_upsert_file_vault_existing_task(vault_db):
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', '/docs/a.txt')")
        conn.commit()
    manager.upsert_file_vault(vault_db, 'h1', 'vault-b', '//nas/a.txt')
    with _connect(vault_db) as conn:
        rows = conn.execute("SELECT * FROM file_vault WHERE file_hash='h1'").fetchall()
        task = conn.execute("SELECT vault_id, file_path FROM tasks WHERE file_hash='h1'").fetchone()
    assert len(rows) == 2
    assert task['vault_id'] == 'vault-a'   # tasks row unchanged
    assert task['file_path'] == '/docs/a.txt'


# ── Test 5 ─────────────────────────────────────────────────────────────────────

def test_upsert_file_vault_path_update_preserves_added_at(vault_db):
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path, added_at) VALUES ('h1', 'vault-a', '/docs/a.txt', '2024-01-01T00:00:00')")
        conn.commit()
    manager.upsert_file_vault(vault_db, 'h1', 'vault-a', '/docs/renamed.txt')
    with _connect(vault_db) as conn:
        row = conn.execute("SELECT * FROM file_vault WHERE file_hash='h1' AND vault_id='vault-a'").fetchone()
    assert row['file_path'] == os.path.normpath('/docs/renamed.txt')
    assert row['added_at'] == '2024-01-01T00:00:00'   # preserved


# ── Test 6 ─────────────────────────────────────────────────────────────────────

def test_get_filtered_hashes_multi_vault(vault_db):
    # Use os.path.normpath so paths are consistent with what upsert_file_vault stores.
    path_a = os.path.normpath('/docs/a.txt')
    path_b = os.path.normpath('Z:/nas/a.txt')   # Z: prefix keeps normpath stable on Windows
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'txt', 'vault-a')", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b,))
        conn.commit()
    hashes_a = manager.get_filtered_hashes(vault_db, vault_ids=['vault-a'])
    hashes_b = manager.get_filtered_hashes(vault_db, vault_ids=['vault-b'])
    assert 'h1' in hashes_a
    assert 'h1' in hashes_b


# ── Test 7 ─────────────────────────────────────────────────────────────────────

def test_fts_search_vault_filter_returns_vault_path(vault_db):
    path_a = os.path.normpath('/docs/a.txt')
    path_b = os.path.normpath('Z:/nas/a.txt')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'txt', 'vault-a')", (path_a,))
        conn.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES ('h1', 0, ?, 'hello world')", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b,))
        conn.commit()
    results = manager.fts_search(vault_db, 'hello', vault_ids=['vault-b'])
    assert len(results) == 1
    assert results[0]['file_path'] == path_b

    # Multi-vault filter: file belongs to both vaults → 2 results, one per vault path
    results_multi = manager.fts_search(vault_db, 'hello', vault_ids=['vault-a', 'vault-b'])
    assert len(results_multi) == 2
    paths_multi = {r['file_path'] for r in results_multi}
    assert path_a in paths_multi
    assert path_b in paths_multi


# ── Test 8 ─────────────────────────────────────────────────────────────────────

def test_filename_search_vault_filter(vault_db):
    path_photo_a = os.path.normpath('/docs/photo.jpg')
    path_photo_b = os.path.normpath('Z:/nas/photo.jpg')
    path_report  = os.path.normpath('/docs/report.pdf')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', ?, 'jpg', 'vault-a')", (path_photo_a,))
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h2', ?, 'pdf', 'vault-a')", (path_report,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', ?)", (path_photo_a,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_photo_b,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h2', 'vault-a', ?)", (path_report,))
        conn.commit()
    results = manager.filename_search(vault_db, 'photo', vault_ids=['vault-b'])
    assert len(results) == 1
    assert results[0]['file_hash'] == 'h1'
    results_no_filter = manager.filename_search(vault_db, 'photo')
    assert len(results_no_filter) == 1


# ── Test 9 ─────────────────────────────────────────────────────────────────────

def test_get_vault_paths(vault_db):
    path_b1 = os.path.normpath('Z:/nas/a.txt')
    path_b2 = os.path.normpath('Z:/nas/b.txt')
    with _connect(vault_db) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h1', '/docs/a.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, vault_id) VALUES ('h2', '/docs/b.txt', 'txt', 'vault-a')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-b', ?)", (path_b1,))
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h2', 'vault-b', ?)", (path_b2,))
        conn.commit()
    paths = manager.get_vault_paths(vault_db, ['h1', 'h2'], 'vault-b')
    assert paths['h1'] == path_b1
    assert paths['h2'] == path_b2


# ── Test 10 ────────────────────────────────────────────────────────────────────

def test_ingestor_registers_second_vault(tmp_path):
    """Same content, two vault scan paths → both registered in file_vault."""
    import hashlib
    from unittest.mock import patch
    from core import ingestor

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)

    content = b'shared photo content'
    expected_hash = hashlib.sha256(content).hexdigest()

    dir_a = tmp_path / "vault_a"
    dir_a.mkdir()
    (dir_a / "photo.jpg").write_bytes(content)

    dir_b = tmp_path / "vault_b"
    dir_b.mkdir()
    (dir_b / "photo.jpg").write_bytes(content)

    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'My Docs', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_a),))
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-b', 'NAS Photos', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_b),))
        conn.commit()

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
        ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM file_vault WHERE file_hash=?", (expected_hash,)
        ).fetchall()

    vault_ids_found = {r['vault_id'] for r in rows}
    assert 'vault-a' in vault_ids_found
    assert 'vault-b' in vault_ids_found
    assert len(rows) == 2


# ── Test 12 ────────────────────────────────────────────────────────────────────

def test_ingestor_move_non_origin_vault_does_not_corrupt_tasks_path(tmp_path):
    """When non-origin vault detects a path change, tasks.file_path is NOT updated."""
    import hashlib
    from unittest.mock import patch
    from core import ingestor

    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)

    content = b'some content'
    expected_hash = hashlib.sha256(content).hexdigest()

    dir_a = tmp_path / "vault_a"
    dir_a.mkdir()
    (dir_a / "photo.jpg").write_bytes(content)

    dir_b = tmp_path / "vault_b"
    dir_b.mkdir()
    (dir_b / "nas_photo.jpg").write_bytes(content)  # same content, different name

    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'Origin', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_a),))
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-b', 'NAS', ?, 5, 'active', '2024-01-01', '2024-01-01')""",
                     (str(dir_b),))
        conn.commit()

    with patch('core.ingestor._is_hidden_or_system', return_value=False):
        ingestor.ingest(str(dir_a), db_path, vault_id='vault-a')
        ingestor.ingest(str(dir_b), db_path, vault_id='vault-b')

    with _connect(db_path) as conn:
        task = conn.execute(
            "SELECT file_path, vault_id FROM tasks WHERE file_hash=?", (expected_hash,)
        ).fetchone()
        fv_b = conn.execute(
            "SELECT file_path FROM file_vault WHERE file_hash=? AND vault_id='vault-b'",
            (expected_hash,)
        ).fetchone()

    assert 'photo.jpg' in task['file_path']    # origin path unchanged
    assert task['vault_id'] == 'vault-a'
    assert 'nas_photo.jpg' in fv_b['file_path']  # vault-b has its own path
