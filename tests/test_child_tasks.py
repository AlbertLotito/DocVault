"""
Child tasks: images the PDF harvester cuts out into the extracted-images cache.

They are processing artifacts, reachable only through their parent PDF:
- they never get file_vault rows. The init_db backfill gave them rows with
  '.cache\\…' paths, vault scans never see .cache, so the deleted-file detector
  flagged all 39,690 MISSING (only 138 of 39,828 MISSING files were real).
- they are not indexed as standalone search results (no FTS rows, no embedding);
  their useful content reaches the parent via write-back.
- when a parent PDF is re-extracted, its existing children are re-queued so they
  run again and write back into the fresh parent text.
"""
import sqlite3

import pytest

from core import manager


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'test.db')
    manager.init_db(path)
    with manager._connect(path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V', '/v', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return path


def _q(db, sql, *args):
    with sqlite3.connect(db) as c:
        return c.execute(sql, args).fetchall()


def _parent_and_child(db, child_status='COMPLETED'):
    manager.insert_task(db, 'P', '/v/scan.pdf', 'pdf', vault_id='v1')
    manager.upsert_file_vault(db, 'P', 'v1', '/v/scan.pdf')
    manager.insert_child_tasks(db, [{'file_hash': 'C', 'file_path': '.cache/extracted_images/scan_1/page_001_img_001.png',
                                     'file_type': 'png', 'parent_hash': 'P', 'vault_id': 'v1'}])
    with sqlite3.connect(db) as c:
        c.execute("UPDATE tasks SET status=? WHERE file_hash='C'", (child_status,))


def test_init_db_backfill_skips_child_tasks(db):
    _parent_and_child(db)
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM file_vault")
    manager.init_db(db)
    assert _q(db, "SELECT file_hash FROM file_vault") == [('P',)]


def test_init_db_repairs_children_wrongly_flagged_missing(db):
    _parent_and_child(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO file_vault (file_hash, vault_id, file_path, miss_count) VALUES ('C', 'v1', 'x', 3)")
        c.execute("UPDATE tasks SET pre_missing_status='COMPLETED', status='MISSING' WHERE file_hash='C'")
    manager.init_db(db)
    assert _q(db, "SELECT status, pre_missing_status FROM tasks WHERE file_hash='C'") == [('COMPLETED', None)]
    assert _q(db, "SELECT count(*) FROM file_vault WHERE file_hash='C'") == [(0,)]


def test_missing_detection_never_flags_children(db, monkeypatch):
    from core import ingestor
    _parent_and_child(db)
    with sqlite3.connect(db) as c:   # a stray child row must still be ignored
        c.execute("INSERT INTO file_vault (file_hash, vault_id, file_path, miss_count) VALUES ('C', 'v1', 'x', 99)")
    import os
    for _ in range(5):
        ingestor._update_missing_flags(db, 'v1', {os.path.normpath('/v/scan.pdf')})
    assert _q(db, "SELECT status FROM tasks WHERE file_hash='C'") == [('COMPLETED',)]


def test_child_text_is_not_indexed_in_fts(db):
    _parent_and_child(db, 'PROCESSING')
    manager.complete_extraction(db, 'C', 'COMPLETED', text='Page one of the lease agreement.')
    assert _q(db, "SELECT count(*) FROM fts_index WHERE file_hash='C'") == [(0,)]
    assert _q(db, "SELECT count(*) FROM extracted_texts WHERE file_hash='C'") == [(1,)]   # kept for inspection


def test_filename_search_excludes_children(db):
    _parent_and_child(db)
    names = [r['file_hash'] for r in manager.filename_search(db, 'page_001', 50)]
    assert names == []
    assert [r['file_hash'] for r in manager.filename_search(db, 'scan', 50)] == ['P']


@pytest.mark.parametrize('old_status', ['COMPLETED', 'ERROR', 'MISSING'])
def test_redispatching_children_requeues_them(db, old_status):
    _parent_and_child(db, old_status)
    with sqlite3.connect(db) as c:
        c.execute("INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text) VALUES ('C', 'old narrative')")
    manager.insert_child_tasks(db, [{'file_hash': 'C', 'file_path': '.cache/extracted_images/scan_1/page_001_img_001.png',
                                     'file_type': 'png', 'parent_hash': 'P', 'vault_id': 'v1'}])
    assert _q(db, "SELECT status FROM tasks WHERE file_hash='C'") == [('PENDING',)]
    assert _q(db, "SELECT count(*) FROM extracted_texts WHERE file_hash='C'") == [(0,)]


def test_redispatch_leaves_in_flight_children_alone(db):
    _parent_and_child(db, 'PROCESSING')
    manager.insert_child_tasks(db, [{'file_hash': 'C', 'file_path': 'x', 'file_type': 'png', 'parent_hash': 'P'}])
    assert _q(db, "SELECT status FROM tasks WHERE file_hash='C'") == [('PROCESSING',)]


def test_worker_completes_children_without_embedding():
    from unittest.mock import MagicMock, patch
    from workers import extraction_worker
    k = MagicMock(); k.__name__ = 'intelligent_image_extractor'
    k.extract.return_value = ('A photo of a dog on a beach.', None, {})
    with patch('workers.extraction_worker.router.get_extractors', return_value=[k]), \
         patch('workers.extraction_worker.manager.complete_extraction') as done, \
         patch('workers.extraction_worker.manager.append_parent_text'), \
         patch('workers.extraction_worker.manager.reset_to_extracted_if_complete'):
        extraction_worker.process_task('t.db', {'file_hash': 'C', 'file_path': 'x.png', 'file_type': 'png',
                                                'parent_hash': 'P', 'metadata_json': '{}'})
    assert done.call_args[1]['status'] == 'COMPLETED'


def test_purging_a_parent_also_purges_its_children(db):
    """tasks.parent_hash REFERENCES tasks(file_hash) without cascade: purging a
    MISSING PDF that had harvested images raised 'FOREIGN KEY constraint failed'
    (HTTP 500 on Purge, found in UI testing)."""
    _parent_and_child(db)
    manager.insert_child_tasks(db, [{'file_hash': 'C2', 'file_path': '.cache/extracted_images/scan_1/page_002_img_001.png',
                                     'file_type': 'png', 'parent_hash': 'P', 'vault_id': 'v1'}])
    manager.insert_task(db, 'OTHER', '/v/other.pdf', 'pdf', vault_id='v1')
    with sqlite3.connect(db) as c:
        c.execute("INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text) VALUES ('C', 'child text')")

    manager.purge_file_hashes(db, ['P'])

    assert _q(db, "SELECT file_hash FROM tasks ORDER BY file_hash") == [('OTHER',)]
    assert _q(db, "SELECT count(*) FROM extracted_texts WHERE file_hash IN ('C','C2')") == [(0,)]


def test_purge_missing_returns_only_the_requested_files(db):
    _parent_and_child(db)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE tasks SET status='MISSING' WHERE file_hash='P'")
    assert manager.purge_missing_files(db, ['P']) == ['P']
    assert _q(db, "SELECT count(*) FROM tasks") == [(0,)]
