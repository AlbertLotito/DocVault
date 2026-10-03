"""
Per-file FTS deletes must not scan the whole fts_index.

fts_index.file_hash is UNINDEXED (FTS5), so `DELETE ... WHERE file_hash = ?`
scans every row: ~6.7s per file on the live 4.25M-row index, paid on every
extraction. fts_spans records each file's rowid range so deletes are a range
lookup instead.
"""
import sqlite3

import pytest

from core import manager


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / 'test.db')
    manager.init_db(db_path)
    return db_path


def _add(db, file_hash, text):
    manager.insert_task(db, file_hash, f'/docs/{file_hash}.txt', 'txt')
    manager.complete_extraction(db, file_hash, 'EXTRACTED', text=text)


def _rows(db, file_hash=None):
    with sqlite3.connect(db) as c:
        if file_hash:
            return c.execute("SELECT COUNT(*) FROM fts_index WHERE file_hash = ?", (file_hash,)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM fts_index").fetchone()[0]


def _big_text(n_sentences, word):
    return ' '.join(f'{word} sentence number {i} with some padding text.' for i in range(n_sentences))


def _vm_steps(db, fn):
    """Count SQLite VM instructions executed by fn(conn) — a stable proxy for work done."""
    steps = [0]

    def tick():
        steps[0] += 1
        return 0

    with manager._connect(db) as conn:
        conn.set_progress_handler(tick, 100)
        fn(conn)
        conn.commit()
    return steps[0] * 100


def test_reextraction_replaces_only_that_files_rows(db):
    _add(db, 'a', _big_text(300, 'alpha'))
    _add(db, 'b', _big_text(300, 'bravo'))
    before_b = _rows(db, 'b')

    manager.complete_extraction(db, 'a', 'EXTRACTED', text='short replacement text.')

    assert _rows(db, 'a') == 1
    assert _rows(db, 'b') == before_b


def test_update_fts_replaces_rows(db):
    _add(db, 'a', _big_text(300, 'alpha'))
    with sqlite3.connect(db) as c:
        c.execute("UPDATE extracted_texts SET extracted_text = 'tiny.' WHERE file_hash = 'a'")
    manager.update_fts(db, 'a')
    assert _rows(db, 'a') == 1


def test_deleting_one_file_does_not_scan_the_whole_index(db):
    for i in range(30):
        _add(db, f'f{i}', _big_text(400, f'w{i}'))
    _add(db, 'target', _big_text(50, 'target'))
    total = _rows(db)
    target_rows = _rows(db, 'target')

    steps = _vm_steps(db, lambda conn: manager.fts_delete(conn, 'target'))

    assert _rows(db, 'target') == 0
    assert _rows(db) == total - target_rows
    # A full scan touches every row (~10+ VM steps each); a span delete only its own rows.
    assert steps < total * 2, f"{steps} VM steps for {total} rows looks like a full scan"


def test_first_extraction_of_a_new_file_skips_the_delete_scan(db):
    for i in range(30):
        _add(db, f'f{i}', _big_text(400, f'w{i}'))
    total = _rows(db)
    manager.insert_task(db, 'new', '/docs/new.txt', 'txt')

    steps = _vm_steps(db, lambda conn: manager.fts_delete(conn, 'new'))

    assert steps < 1000
    assert _rows(db) == total


def test_purge_removes_rows_and_spans(db):
    _add(db, 'a', _big_text(100, 'alpha'))
    _add(db, 'b', _big_text(100, 'bravo'))
    manager.purge_file_hashes(db, ['a'])
    assert _rows(db, 'a') == 0 and _rows(db, 'b') > 0
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM fts_spans WHERE file_hash = 'a'").fetchone()[0] == 0


def test_existing_index_is_backfilled_on_init(tmp_path):
    """Databases created before fts_spans existed get spans built from fts_index."""
    db = str(tmp_path / 'old.db')
    manager.init_db(db)
    _add(db, 'a', _big_text(100, 'alpha'))
    _add(db, 'b', _big_text(100, 'bravo'))
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE fts_spans")          # simulate a pre-fts_spans database

    manager.init_db(db)

    with sqlite3.connect(db) as c:
        spans = dict(c.execute("SELECT file_hash, last_rowid - first_rowid + 1 FROM fts_spans").fetchall())
    assert spans == {'a': _rows(db, 'a'), 'b': _rows(db, 'b')}
    with manager._connect(db) as conn:
        manager.fts_delete(conn, 'a')
        conn.commit()
    assert _rows(db, 'a') == 0 and _rows(db, 'b') > 0


def test_delete_is_correct_even_if_rows_are_not_contiguous(db):
    """Interleaved inserts (e.g. rows written by an older code path) must never
    let a span delete remove another file's rows."""
    with sqlite3.connect(db) as c:
        for i in range(6):
            fh = 'a' if i % 2 == 0 else 'b'
            c.execute("INSERT INTO fts_index (file_hash, chunk_index, file_path, content) VALUES (?, ?, '/x', 'word')", (fh, i))
        c.execute("DELETE FROM fts_spans")
        c.execute("""INSERT INTO fts_spans (file_hash, first_rowid, last_rowid)
                     SELECT file_hash, MIN(rowid), MAX(rowid) FROM fts_index GROUP BY file_hash""")
    with manager._connect(db) as conn:
        manager.fts_delete(conn, 'a')
        conn.commit()
    assert _rows(db, 'a') == 0 and _rows(db, 'b') == 3
