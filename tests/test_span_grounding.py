"""Tests for span grounding offset resolution."""

CHUNK_SIZE = 600
OVERLAP = 100
STRIDE = CHUNK_SIZE - OVERLAP  # 500


def _make_text(n_chunks: int) -> str:
    """Build ASCII text exactly large enough to produce n_chunks at default settings."""
    return 'A' * (STRIDE * (n_chunks - 1) + CHUNK_SIZE)


# ── resolve_offset ────────────────────────────────────────────────────────────

def test_formula_offset_chunk_zero():
    from search.spans import resolve_offset
    text = _make_text(3)
    chunk_text = text[0:CHUNK_SIZE]
    assert resolve_offset(0, chunk_text, text, CHUNK_SIZE, OVERLAP) == 0


def test_formula_offset_chunk_one():
    from search.spans import resolve_offset
    text = _make_text(3)
    chunk_text = text[STRIDE:STRIDE + CHUNK_SIZE]
    assert resolve_offset(1, chunk_text, text, CHUNK_SIZE, OVERLAP) == STRIDE


def test_formula_offset_last_chunk():
    from search.spans import resolve_offset
    from embeddings.chunker import chunk
    text = _make_text(3) + 'X' * 50  # last chunk shorter than CHUNK_SIZE
    chunks = chunk(text, CHUNK_SIZE, OVERLAP)
    last_idx = len(chunks) - 1
    result = resolve_offset(last_idx, chunks[last_idx], text, CHUNK_SIZE, OVERLAP)
    assert result == last_idx * STRIDE


def test_fallback_text_search_on_wrong_index():
    from search.spans import resolve_offset
    from embeddings.chunker import chunk
    text = 'Hello world. ' * 60
    chunks = chunk(text, CHUNK_SIZE, OVERLAP)
    chunk_text = chunks[1]
    # Pass wrong chunk_index to force formula mismatch → fallback
    result = resolve_offset(99, chunk_text, text, CHUNK_SIZE, OVERLAP)
    assert result == text.find(chunk_text[:120])


def test_resolve_none_when_both_fail():
    from search.spans import resolve_offset
    result = resolve_offset(0, 'definitely not in here', 'completely different text',
                            CHUNK_SIZE, OVERLAP)
    assert result is None


def test_resolve_none_on_empty_extracted_text():
    from search.spans import resolve_offset
    result = resolve_offset(0, 'chunk', '', CHUNK_SIZE, OVERLAP)
    assert result is None


# ── paragraph_number ──────────────────────────────────────────────────────────

def test_paragraph_number_at_start():
    from search.spans import paragraph_number
    text = 'Para1\n\nPara2\n\nPara3'
    assert paragraph_number(text, 0) == 1


def test_paragraph_number_second_paragraph():
    from search.spans import paragraph_number
    text = 'Para1\n\nPara2\n\nPara3'
    assert paragraph_number(text, text.index('Para2')) == 2


def test_paragraph_number_third_paragraph():
    from search.spans import paragraph_number
    text = 'Para1\n\nPara2\n\nPara3'
    assert paragraph_number(text, text.index('Para3')) == 3


def test_paragraph_number_no_breaks():
    from search.spans import paragraph_number
    text = 'Single paragraph text here'
    assert paragraph_number(text, 10) == 1


# ── manager.get_extracted_texts ───────────────────────────────────────────────

def test_get_extracted_texts_returns_dict(tmp_path):
    """get_extracted_texts returns a {hash: text} dict for known hashes."""
    import sqlite3
    from core import manager

    db = str(tmp_path / 'test.db')
    manager.init_db(db)

    # Insert into extracted_texts (not tasks.extracted_text — that column doesn't exist)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (file_hash, file_path, file_type, status) "
            "VALUES ('aaa', '/a.txt', 'txt', 'COMPLETED')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO tasks (file_hash, file_path, file_type, status) "
            "VALUES ('bbb', '/b.txt', 'txt', 'COMPLETED')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at) "
            "VALUES ('aaa', 'hello world', datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at) "
            "VALUES ('bbb', 'foo bar', datetime('now'))"
        )
        conn.commit()

    result = manager.get_extracted_texts(db, ['aaa', 'bbb'])
    assert result == {'aaa': 'hello world', 'bbb': 'foo bar'}


def test_get_extracted_texts_unknown_hash(tmp_path):
    from core import manager
    db = str(tmp_path / 'test2.db')
    manager.init_db(db)
    result = manager.get_extracted_texts(db, ['nonexistent'])
    assert result == {}


def test_get_extracted_texts_empty_list(tmp_path):
    from core import manager
    db = str(tmp_path / 'test3.db')
    manager.init_db(db)
    result = manager.get_extracted_texts(db, [])
    assert result == {}
