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
