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


# ── GET /api/catalog/{hash}/text ──────────────────────────────────────────────

def test_catalog_text_endpoint_returns_text(tmp_path):
    import sqlite3
    from fastapi.testclient import TestClient
    from core import manager
    import api.main as main_mod

    db = str(tmp_path / 'catalog_test.db')
    manager.init_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (file_hash, file_path, file_type, status) "
            "VALUES ('abc123', '/doc.txt', 'txt', 'COMPLETED')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at) "
            "VALUES ('abc123', 'The full text of the document.', datetime('now'))"
        )
        conn.commit()

    main_mod.DB_PATH = db
    from api.main import app
    client = TestClient(app)

    resp = client.get('/api/catalog/abc123/text')
    assert resp.status_code == 200
    assert resp.json()['extracted_text'] == 'The full text of the document.'


def test_catalog_text_endpoint_404_on_missing(tmp_path):
    from fastapi.testclient import TestClient
    from core import manager
    import api.main as main_mod

    db = str(tmp_path / 'catalog_test2.db')
    manager.init_db(db)
    main_mod.DB_PATH = db
    from api.main import app
    client = TestClient(app)

    resp = client.get('/api/catalog/deadbeef/text')
    assert resp.status_code == 404


# ── llm/base.py paragraph tags ────────────────────────────────────────────────

class _MockProvider:
    """Minimal provider that records the messages it receives."""
    def __init__(self):
        self.last_messages = []
    def chat(self, messages):
        self.last_messages = messages
        return 'answer'

def _system_content(chunks, question='test?'):
    from llm.base import BaseLLMProvider
    p = _MockProvider()
    # Bind _build_rag_messages from BaseLLMProvider onto the mock instance
    import types
    p._build_rag_messages = types.MethodType(BaseLLMProvider._build_rag_messages, p)
    p._extract_thinking = types.MethodType(BaseLLMProvider._extract_thinking, p)
    BaseLLMProvider.rag_query(p, question, chunks)
    return p.last_messages[0]['content']


def test_rag_query_dict_chunks_add_paragraph_attr():
    content = _system_content([{'text': 'some text', 'paragraph_num': 4}])
    assert 'paragraph="4"' in content


def test_rag_query_str_chunks_no_paragraph_attr():
    """Legacy list[str] call still works without paragraph attribute."""
    content = _system_content(['plain string chunk'])
    assert 'paragraph=' not in content


def test_rag_query_dict_null_paragraph_omits_attr():
    content = _system_content([{'text': 'some text', 'paragraph_num': None}])
    assert 'paragraph=' not in content


# ── /api/query source enrichment ─────────────────────────────────────────────

def test_query_sources_include_offset_fields(tmp_path, monkeypatch):
    """POST /query sources include chunk_offset, chunk_size, paragraph_num."""
    import sqlite3
    from fastapi.testclient import TestClient
    from core import manager
    import api.main as main_mod

    db = str(tmp_path / 'query_test.db')
    manager.init_db(db)

    extracted = 'Introduction\n\nMain content here. ' * 5
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (file_hash, file_path, file_type, status) "
            "VALUES ('hash1', '/doc.txt', 'txt', 'COMPLETED')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at) "
            "VALUES ('hash1', ?, datetime('now'))", (extracted,)
        )
        conn.commit()

    main_mod.DB_PATH = db

    chunk_text = extracted[0:600]
    fake_result = {
        'file_hash': 'hash1',
        'file_path': '/doc.txt',
        'chunk_index': 0,
        'chunk_text': chunk_text,
        'score': 0.9,
        'combined_score': 0.015,
    }

    async def _fake_search(self, **kwargs):
        return [fake_result], False

    monkeypatch.setattr('api.routes.query.hybrid', type('M', (), {'async_search': _fake_search})())

    class _FakeLLM:
        def rag_query(self, q, chunks, history=None):
            return {'answer': 'ok', 'thinking': None}

    monkeypatch.setattr('api.routes.query.get_provider', lambda: _FakeLLM())

    from api.main import app
    client = TestClient(app)
    resp = client.post('/api/query', json={'question': 'what?'})
    assert resp.status_code == 200
    sources = resp.json()['sources']
    assert len(sources) == 1
    s = sources[0]
    assert 'chunk_offset' in s
    assert 'chunk_size' in s
    assert 'paragraph_num' in s
    assert s['chunk_offset'] == 0
    assert s['paragraph_num'] == 1
    assert 'file_hash' in s


# ── /api/search result enrichment ────────────────────────────────────────────

def test_search_results_include_offset_fields(tmp_path, monkeypatch):
    """GET /search results include chunk_offset, chunk_size, paragraph_num."""
    import sqlite3
    from fastapi.testclient import TestClient
    from core import manager
    import api.main as main_mod

    db = str(tmp_path / 'search_test.db')
    manager.init_db(db)

    extracted = 'First para.\n\nSecond para here. ' * 10
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tasks (file_hash, file_path, file_type, status) "
            "VALUES ('srch1', '/s.txt', 'txt', 'COMPLETED')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO extracted_texts (file_hash, extracted_text, stored_at) "
            "VALUES ('srch1', ?, datetime('now'))", (extracted,)
        )
        conn.commit()

    main_mod.DB_PATH = db

    chunk_text = extracted[0:600]
    fake_results = [{
        'file_hash': 'srch1',
        'file_path': '/s.txt',
        'chunk_index': 0,
        'chunk_text': chunk_text,
        'score': 0.8,
        'combined_score': 0.01,
    }]

    async def _fake_hybrid(self, **kwargs):
        return fake_results, False

    monkeypatch.setattr('api.routes.search.hybrid', type('M', (), {'async_search': _fake_hybrid})())

    from api.main import app
    client = TestClient(app)
    resp = client.get('/api/search?q=para&mode=hybrid')
    assert resp.status_code == 200
    results = resp.json()['results']
    assert len(results) == 1
    r = results[0]
    assert 'chunk_offset' in r
    assert 'chunk_size' in r
    assert 'paragraph_num' in r
    assert r['chunk_offset'] == 0
    assert r['paragraph_num'] == 1
