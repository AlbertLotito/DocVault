"""Shared pytest helpers for DocVault test suite."""
import threading
from unittest.mock import MagicMock


def make_test_ctx(vault_id='test-vault', file_hash='test-hash'):
    """Return a minimal ExtractorContext suitable for unit tests."""
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id=vault_id,
        file_hash=file_hash,
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


import pytest


@pytest.fixture(scope='session')
def _test_lancedb_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp('lancedb_storage'))


@pytest.fixture(autouse=True)
def _isolate_vector_stores(monkeypatch, _test_lancedb_dir):
    """Never let a test touch the real lancedb_storage/ (see test_test_isolation.py)."""
    from embeddings import art_vector_store, vector_store
    monkeypatch.setattr(vector_store, '_db_path', lambda: _test_lancedb_dir)
    monkeypatch.setattr(art_vector_store, '_db_path', lambda: _test_lancedb_dir)
