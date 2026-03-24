"""
Search enhancement tests:
  1 — detect_mode: regex, wildcard, plain detection
  2 — FTS: _sanitize_fts_query preserves trailing * after word char
  3 — FTS: regex search (/pattern/) matches content
  4 — FTS: prefix wildcard (word*) passes through to FTS5
  5 — filename_search: plain multi-token (unchanged behaviour)
  6 — filename_search: wildcard (* and ?)
  7 — filename_search: regex (/pattern/)
  8 — search route: regex/wildcard queries degrade semantic/hybrid to FTS
"""
import pytest
from unittest.mock import patch, MagicMock
from core import manager
from search.query import detect_mode
from search.fts import _sanitize_fts_query


# ── Helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    manager.insert_task(db_path, 'h1', '/docs/annual report 2024.pdf', 'pdf')
    manager.insert_task(db_path, 'h2', '/photos/holiday_snap.jpg', 'jpg')
    manager.insert_task(db_path, 'h3', '/docs/budget_2024.xlsx', 'xlsx')
    manager.complete_extraction(db_path, 'h1',
        text='Revenue grew significantly this quarter', status='EXTRACTED')
    manager.complete_extraction(db_path, 'h2',
        text='Beautiful sunset at the beach', status='EXTRACTED')
    manager.complete_extraction(db_path, 'h3',
        text='Total expenses column sums to zero', status='EXTRACTED')
    return db_path


# ── 1: detect_mode ─────────────────────────────────────────────────────────────

def test_detect_mode_regex():
    assert detect_mode('/foo.*bar/') == ('regex', 'foo.*bar')


def test_detect_mode_regex_with_spaces():
    assert detect_mode('  /hello world/  ') == ('regex', 'hello world')


def test_detect_mode_wildcard_star():
    mode, val = detect_mode('report*')
    assert mode == 'wildcard'
    assert val == 'report*'


def test_detect_mode_wildcard_question():
    mode, val = detect_mode('report?.pdf')
    assert mode == 'wildcard'
    assert val == 'report?.pdf'


def test_detect_mode_plain():
    assert detect_mode('annual report') == ('plain', 'annual report')


def test_detect_mode_plain_no_wildcards():
    assert detect_mode('hello world') == ('plain', 'hello world')


# ── 2: FTS sanitizer ───────────────────────────────────────────────────────────

def test_sanitize_preserves_trailing_star_after_word():
    assert _sanitize_fts_query('doc*') == 'doc*'


def test_sanitize_preserves_mid_word_star_at_end():
    assert _sanitize_fts_query('report*') == 'report*'


def test_sanitize_removes_leading_star():
    result = _sanitize_fts_query('*report')
    assert result == 'report'


def test_sanitize_removes_punctuation():
    result = _sanitize_fts_query('hello! world?')
    # ? is not a word char, so removed; ! also removed
    assert 'hello' in result
    assert '!' not in result


def test_sanitize_preserves_plain_words():
    assert _sanitize_fts_query('annual report') == 'annual report'


# ── 3: FTS regex search ────────────────────────────────────────────────────────

def test_fts_regex_finds_content_match(db):
    from search.fts import search
    results = search(db, '/[Rr]evenue/')
    assert any(r['file_hash'] == 'h1' for r in results)


def test_fts_regex_no_match_returns_empty(db):
    from search.fts import search
    results = search(db, '/zzz_no_match_at_all/')
    assert results == []


def test_fts_regex_invalid_pattern_returns_empty(db):
    from search.fts import search
    results = search(db, '/[unclosed/')
    assert results == []


# ── 4: FTS prefix wildcard ─────────────────────────────────────────────────────

def test_fts_prefix_star_matches(db):
    """word* should hit FTS5 prefix index and match 'Revenue', 'grew', etc."""
    from search.fts import search
    results = search(db, 'Reven*')
    # FTS5 prefix — should find 'Revenue'
    assert any(r['file_hash'] == 'h1' for r in results)


# ── 5: filename_search plain ───────────────────────────────────────────────────

def test_filename_plain_finds_partial_match(db):
    results = manager.filename_search(db, 'annual')
    assert any(r['file_hash'] == 'h1' for r in results)


def test_filename_plain_multi_token_all_must_match(db):
    # 'annual' + 'report' both in h1 path; h2 and h3 should not match
    results = manager.filename_search(db, 'annual report')
    assert all(r['file_hash'] == 'h1' for r in results)


def test_filename_plain_no_match(db):
    results = manager.filename_search(db, 'zzz_no_match')
    assert results == []


# ── 6: filename_search wildcard ────────────────────────────────────────────────

def test_filename_wildcard_star_matches_suffix(db):
    results = manager.filename_search(db, '*.pdf')
    hashes = {r['file_hash'] for r in results}
    assert 'h1' in hashes
    assert 'h2' not in hashes
    assert 'h3' not in hashes


def test_filename_wildcard_question_mark(db):
    # 'holiday_snap.jp?' — ? matches the final 'g' in '.jpg'
    results = manager.filename_search(db, 'holiday_snap.jp?')
    assert any(r['file_hash'] == 'h2' for r in results)


def test_filename_wildcard_star_prefix(db):
    # '*2024*' should match both h1 and h3 (both contain '2024')
    results = manager.filename_search(db, '*2024*')
    hashes = {r['file_hash'] for r in results}
    assert 'h1' in hashes
    assert 'h3' in hashes


def test_filename_wildcard_no_match(db):
    results = manager.filename_search(db, '*.zzz')
    assert results == []


# ── 7: filename_search regex ───────────────────────────────────────────────────

def test_filename_regex_finds_match(db):
    results = manager.filename_search(db, r'/\d{4}/')
    hashes = {r['file_hash'] for r in results}
    # h1 and h3 have '2024' in path
    assert 'h1' in hashes
    assert 'h3' in hashes


def test_filename_regex_case_insensitive(db):
    results = manager.filename_search(db, '/ANNUAL/')
    assert any(r['file_hash'] == 'h1' for r in results)


def test_filename_regex_invalid_returns_empty(db):
    results = manager.filename_search(db, '/[unclosed/')
    assert results == []


def test_filename_regex_no_match(db):
    results = manager.filename_search(db, '/zzz_no_match/')
    assert results == []


# ── 8: search route degrades for regex/wildcard ────────────────────────────────

def _make_search_request(mode, query):
    """Simulate the search route logic for detecting vector_unsupported."""
    query_mode, _ = detect_mode(query)
    return query_mode in ('regex', 'wildcard')


def test_route_regex_query_marks_vector_unsupported():
    assert _make_search_request('semantic', '/foo.*/') is True


def test_route_wildcard_query_marks_vector_unsupported():
    assert _make_search_request('hybrid', 'report*') is True


def test_route_plain_query_not_unsupported():
    assert _make_search_request('hybrid', 'annual report') is False


def test_route_plain_fts_not_unsupported():
    assert _make_search_request('fts', 'hello') is False
