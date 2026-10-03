"""Tests must never touch the real vector stores in lancedb_storage/.

test_vault_ignores once ran deletes (file_hash = 'ddeeff...') against the live
LanceDB table: harmless no-ops, but each created a table version, and they could
race a running server or a compaction.
"""
import os

PROD = os.path.normcase(os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lancedb_storage')))


def test_vector_store_is_redirected_away_from_production():
    from embeddings import vector_store
    assert os.path.normcase(os.path.abspath(vector_store._db_path())) != PROD


def test_art_vector_store_is_redirected_away_from_production():
    from embeddings import art_vector_store
    assert os.path.normcase(os.path.abspath(art_vector_store._db_path())) != PROD
