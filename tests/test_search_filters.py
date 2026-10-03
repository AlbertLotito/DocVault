"""
file_type / date filters must mean the same thing in every search path:
full-text (with and without a vault filter), regex full-text, the hash filter
behind semantic/hybrid search, and filename search.

Before: content searches matched file_type as a substring ('doc' also matched
'docx') and compared dates as strings against file_modified only. That dropped
a file modified at 23:59:59.9 on the last day, and files with no modified date
(filename search fell back to the created date). See docs/search-api.md §6.
"""
import sqlite3

import pytest

from core import manager
from search import fts

FILES = {
    # hash: (path, type, modified, created)
    'a': ('/v/report.doc',  'doc',  '2024-12-31T23:59:59.900000', '2024-01-01T00:00:00'),
    'b': ('/v/report.docx', 'docx', '2024-06-01T10:00:00',        '2024-01-01T00:00:00'),
    'c': ('/v/scan.pdf',    'pdf',  None,                         '2024-12-15T09:00:00'),
    'd': ('/v/late.pdf',    'pdf',  '2025-01-01T00:00:01',        '2024-01-01T00:00:00'),
}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'test.db')
    manager.init_db(path)
    with manager._connect(path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V', '/v', 5, 'active', '2024-01-01', '2024-01-01')""")
        for h, (p, t, mod, cre) in FILES.items():
            conn.execute("""INSERT INTO tasks (file_hash, file_path, file_type, status, file_modified, file_created)
                            VALUES (?, ?, ?, 'COMPLETED', ?, ?)""", (h, p, t, mod, cre))
            conn.execute("INSERT INTO file_vault (file_hash, vault_id, file_path) VALUES (?, 'v1', ?)", (h, p))
            manager.fts_insert(conn, h, p, [f'alpha report {h}'])
        conn.commit()
    return path


def _all_paths(db, **filters):
    """{path name: set of hashes} for the same filters through every search path."""
    hashes = lambda rows: {r['file_hash'] for r in rows}
    return {
        'fts': hashes(fts.search(db, 'alpha', 50, **filters)),
        'fts+vault': hashes(fts.search(db, 'alpha', 50, vault_ids=['v1'], **filters)),
        'regex': hashes(fts.search(db, '/alph./', 50, **filters)),
        'regex+vault': hashes(fts.search(db, '/alph./', 50, vault_ids=['v1'], **filters)),
        'semantic filter': set(manager.get_filtered_hashes(db, **filters)),
        'semantic filter+vault': set(manager.get_filtered_hashes(db, vault_ids=['v1'], **filters)),
        'filename': hashes(manager.filename_search(db, 'report', 50, **filters))
                    | hashes(manager.filename_search(db, 'scan', 50, **filters))
                    | hashes(manager.filename_search(db, 'late', 50, **filters)),
    }


def test_file_type_is_an_exact_case_insensitive_extension_everywhere(db):
    for path, got in _all_paths(db, file_type='doc').items():
        assert got == {'a'}, f"{path}: 'doc' must not match 'docx' -> {got}"
    for path, got in _all_paths(db, file_type='.DOCX').items():
        assert got == {'b'}, f"{path}: '.DOCX' should match docx -> {got}"


def test_date_range_is_inclusive_whole_days_with_created_fallback_everywhere(db):
    for path, got in _all_paths(db, date_from='2024-12-01', date_to='2024-12-31').items():
        # a: last second of the last day; c: no modified date, created in range
        assert got == {'a', 'c'}, f"{path} -> {got}"


def test_date_from_alone(db):
    for path, got in _all_paths(db, date_from='2025-01-01').items():
        assert got == {'d'}, f"{path} -> {got}"
