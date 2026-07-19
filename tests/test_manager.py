import pytest
import os
import tempfile
from core import manager


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


def test_init_creates_tables(db):
    import sqlite3
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    conn.close()
    assert 'tasks' in tables
    assert 'extracted_images' in tables
    # settings lives in settings.db (init_settings_db), not the main DB


def test_insert_task(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    task = manager.get_task(db, 'abc123')
    assert task['file_hash'] == 'abc123'
    assert task['file_path'] == '/docs/test.pdf'
    assert task['file_type'] == 'pdf'
    assert task['status'] == 'PENDING'


def test_insert_task_deduplicates(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')  # duplicate
    result = manager.list_tasks(db)
    assert len(result['tasks']) == 1


def test_update_task_status(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.update_task_status(db, 'abc123', 'EXTRACTING')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTING'


def test_complete_task_stores_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTED'
    # Text lives in extracted_texts, not in the task row
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT extracted_text FROM extracted_texts WHERE file_hash=?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row['extracted_text'] == 'Hello world'


def test_complete_task_stores_error(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', status='ERROR', error='File not found')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'ERROR'
    assert 'File not found' in task['error_log']


def test_claim_pending_task(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    task = manager.claim_pending_task(db, 'worker-1')
    assert task is not None
    assert task['file_hash'] == 'abc123'
    assert task['status'] == 'PROCESSING'


def test_claim_returns_none_when_empty(db):
    task = manager.claim_pending_task(db, 'worker-1')
    assert task is None


def test_get_stats(db):
    manager.insert_task(db, 'a', '/docs/a.pdf', 'pdf')
    manager.insert_task(db, 'b', '/docs/b.txt', 'txt')
    manager.complete_extraction(db, 'b', text='hi', status='EXTRACTED')
    stats = manager.get_stats(db)
    assert stats['total'] == 2
    assert stats['pending'] == 1
    assert stats['extracted'] == 1


def test_insert_extracted_image(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    result = manager.insert_extracted_image(db, 'abc123', {
        'file_path': '/docs/test/page_001_img_001.png',
        'page_num': 1, 'image_index': 1, 'width': 816, 'height': 1056
    })
    assert result is True


def test_fts_search(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='The quick brown fox', status='EXTRACTED')
    results = manager.fts_search(db, 'brown fox')
    assert len(results) == 1
    assert results[0]['file_hash'] == 'abc123'


def test_get_pause_state_defaults_false(db):
    # get_pause_state reads from settings.db, not the task DB
    assert manager.get_pause_state() is False


def test_set_pause_state(db):
    manager.set_pause_state(True)
    assert manager.get_pause_state() is True
    manager.set_pause_state(False)
    assert manager.get_pause_state() is False


def test_extracted_texts_table_exists(db):
    """extracted_texts table must exist with correct columns after init_db."""
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    cols = {r['name'] for r in conn.execute("PRAGMA table_info(extracted_texts)")}
    conn.close()
    assert 'file_hash' in cols
    assert 'extracted_text' in cols
    assert 'stored_at' in cols


def test_extracted_texts_cascade_delete(db):
    """Deleting a task must cascade-delete its extracted_texts row."""
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO extracted_texts (file_hash, extracted_text) VALUES (?, ?)",
        ('abc123', 'Some text')
    )
    conn.commit()
    conn.execute("DELETE FROM tasks WHERE file_hash = ?", ('abc123',))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM extracted_texts WHERE file_hash = ?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row is None, "ON DELETE CASCADE did not fire — extracted_texts row still exists"


def test_indexes_exist(db):
    """Verify all expected indexes are present after init_db."""
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    idx = {r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    conn.close()
    expected = {
        'idx_tasks_status',
        'idx_tasks_vault_id',
        'idx_tasks_file_type',
        'idx_tasks_status_vault',
        'idx_tasks_status_priority',
        'idx_tasks_last_update',
        'idx_tasks_file_path',
        'idx_extracted_images_source_hash',
        'idx_face_detections_cluster_id',
    }
    missing = expected - idx
    assert not missing, f"Missing indexes: {missing}"


def test_complete_extraction_stores_text_in_extracted_texts(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row['extracted_text'] == 'Hello world'


def test_complete_extraction_does_not_store_text_in_tasks(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    task = manager.get_task(db, 'abc123')
    assert task['status'] == 'EXTRACTED'
    assert 'file_hash' in task
    assert 'extracted_text' not in task or task.get('extracted_text') is None


def test_claim_extracted_tasks_returns_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=1)
    assert len(tasks) == 1
    assert tasks[0]['extracted_text'] == 'Hello world'


def test_reprocess_task_clears_extracted_text(db):
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    manager.complete_extraction(db, 'abc123', text='Hello world', status='EXTRACTED')
    manager.reprocess_task(db, 'abc123')
    import sqlite3 as _sq
    conn = _sq.connect(db)
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", ('abc123',)
    ).fetchone()
    conn.close()
    assert row is None


def test_claim_extracted_tasks_skips_missing_text(db):
    """Tasks with EXTRACTED status but no extracted_texts row must not be claimed."""
    import sqlite3 as _sq
    manager.insert_task(db, 'abc123', '/docs/test.pdf', 'pdf')
    # Force status to EXTRACTED without writing text
    conn = _sq.connect(db)
    conn.execute("UPDATE tasks SET status='EXTRACTED' WHERE file_hash='abc123'")
    conn.commit()
    conn.close()
    tasks = manager.claim_extracted_tasks(db, 'worker-1', limit=1)
    assert tasks == []


def test_append_parent_text_nonexistent_parent(db):
    """append_parent_text with a non-existent parent hash must return without raising."""
    manager.append_parent_text(db, 'nonexistent_hash', 'some suffix text')


def test_purge_file_hashes_removes_all_rows(tmp_path):
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/v1', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type) VALUES ('h1', '/a.txt', 'txt')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type) VALUES ('h2', '/b.txt', 'txt')")
        conn.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES ('h1', 0, '/a.txt', 'hello')")
        conn.execute("INSERT INTO extracted_images (source_hash, file_path) VALUES ('h1', '/img.png')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'v1', '/a.txt')")
        conn.commit()

    manager.purge_file_hashes(db_path, ['h1'])

    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h2'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fts_index WHERE file_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM extracted_images WHERE source_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM file_vault WHERE file_hash='h1'").fetchone()[0] == 0


def test_purge_missing_files_only_purges_missing_status(tmp_path):
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a.txt', 'txt', 'MISSING')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/b.txt', 'txt', 'COMPLETED')")
        conn.commit()

    purged = manager.purge_missing_files(db_path, ['h1', 'h2'])

    assert purged == ['h1']
    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h2'").fetchone()[0] == 1  # untouched


def test_purge_missing_files_by_filter(tmp_path):
    from core.manager import _connect
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('vault-a', 'A', '/a', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h1', '/a/report.txt', 'txt', 'MISSING')")
        conn.execute("INSERT INTO tasks (file_hash, file_path, file_type, status) VALUES ('h2', '/a/photo.jpg', 'jpg', 'MISSING')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h1', 'vault-a', '/a/report.txt')")
        conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES ('h2', 'vault-a', '/a/photo.jpg')")
        conn.commit()

    purged = manager.purge_missing_files_by_filter(db_path, q='report')

    assert purged == ['h1']
    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE file_hash='h2'").fetchone()[0] == 1  # untouched
    # No assertion needed beyond "did not raise"
