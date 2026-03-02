import pytest
import tempfile
from core import manager
from search import fts, hybrid
from unittest.mock import patch, MagicMock


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    manager.insert_task(db_path, 'h1', '/docs/report.pdf', 'pdf')
    manager.insert_task(db_path, 'h2', '/docs/notes.txt', 'txt')
    manager.complete_extraction(db_path, 'h1',
        text='The quick brown fox jumps over the lazy dog', status='EXTRACTED')
    manager.complete_extraction(db_path, 'h2',
        text='Python is a great programming language', status='EXTRACTED')
    return db_path


def test_fts_finds_matching_document(db):
    results = fts.search(db, 'brown fox')
    assert len(results) >= 1
    assert any(r['file_hash'] == 'h1' for r in results)


def test_fts_returns_empty_for_no_match(db):
    results = fts.search(db, 'zzz_no_match_zzz')
    assert results == []


def test_fts_result_has_snippet(db):
    results = fts.search(db, 'programming')
    assert len(results) >= 1
    assert 'snippet' in results[0]


@patch('search.semantic.embedder.embed')
@patch('search.semantic.VectorStore')
def test_semantic_search_returns_results(mock_vs_class, mock_embed):
    mock_embed.return_value = [0.1] * 768
    mock_vs = MagicMock()
    mock_vs.search.return_value = [
        {'file_hash': 'h1', 'file_path': '/docs/report.pdf',
         'chunk_text': 'brown fox', 'score': 0.95}
    ]
    mock_vs_class.return_value = mock_vs

    from search import semantic
    results = semantic.search('brown fox', top_k=5)
    assert len(results) == 1
    assert results[0]['score'] == 0.95


def test_hybrid_merges_results(db):
    fts_results = [
        {'file_hash': 'h1', 'file_path': '/docs/a.pdf', 'snippet': 'foo', 'rank': -1.0}
    ]
    sem_results = [
        {'file_hash': 'h1', 'file_path': '/docs/a.pdf', 'chunk_text': 'foo', 'score': 0.9},
        {'file_hash': 'h2', 'file_path': '/docs/b.pdf', 'chunk_text': 'bar', 'score': 0.7},
    ]
    merged = hybrid.merge(fts_results, sem_results)
    hashes = [r['file_hash'] for r in merged]
    assert 'h1' in hashes
    assert 'h2' in hashes
    # h1 appears in both so should rank higher
    assert hashes.index('h1') < hashes.index('h2')
