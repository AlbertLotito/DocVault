import threading, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _make_ctx(tmp_path, monkeypatch):
    import core.manager as m
    ldb = str(tmp_path / 'logs.db')
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: ldb)
    m.init_logs_db()
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='v1',
        file_hash='h1',
        cancel_token=threading.Event(),
        logger=ExtractorLogger('test', 'v1', 'h1'),
        settings=None,
        timeout_secs=None,
    )


def test_ingest_result_defaults():
    from core.extractors.base import IngestResult
    r = IngestResult(text='hello', metadata={})
    assert r.status == 'success'
    assert r.child_tasks == []
    assert r.enrichments == []
    assert r.errors == []
    assert r.elapsed_secs == 0.0


def test_cancel_token_stops_legacy_adapter(tmp_path, monkeypatch):
    """LegacyExtractorAdapter.run() checks cancel_token before calling extract."""
    from core.extractors.base import LegacyExtractorAdapter
    ctx = _make_ctx(tmp_path, monkeypatch)
    ctx.cancel_token.set()  # pre-cancelled

    calls = []
    class FakeExtractor:
        __name__ = 'fake'
        def extract(self, path):
            calls.append(path)
            return ('text', None)

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'cancelled'
    assert calls == []  # extract was never called


def test_legacy_adapter_text_result(tmp_path, monkeypatch):
    from core.extractors.base import LegacyExtractorAdapter
    ctx = _make_ctx(tmp_path, monkeypatch)

    class FakeExtractor:
        __name__ = 'fake'
        def extract(self, path):
            return ('extracted text', None)

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'success'
    assert result.text == 'extracted text'


def test_legacy_adapter_error_only_result(tmp_path, monkeypatch):
    from core.extractors.base import LegacyExtractorAdapter
    ctx = _make_ctx(tmp_path, monkeypatch)

    class FakeExtractor:
        __name__ = 'fake'
        def extract(self, path):
            return (None, 'something went wrong')

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'failed'
    assert len(result.errors) == 1
    assert 'something went wrong' in result.errors[0].message


def test_legacy_adapter_dict_result(tmp_path, monkeypatch):
    from core.extractors.base import LegacyExtractorAdapter
    ctx = _make_ctx(tmp_path, monkeypatch)

    class FakeExtractor:
        __name__ = 'meta'
        def extract(self, path):
            return ({'duration': 42}, None)

    adapter = LegacyExtractorAdapter(FakeExtractor())
    result = adapter.run('fake_path', ctx)
    assert result.status == 'success'
    assert result.metadata == {'duration': 42}


def test_extractor_logger_does_not_raise(tmp_path, monkeypatch):
    """Logger must never raise, even if logs.db is unavailable."""
    import core.manager as m
    monkeypatch.setattr(m, 'get_logs_db_path', lambda: '/nonexistent/path/logs.db')
    from core.extractors.base import ExtractorLogger
    logger = ExtractorLogger('test', 'vault1', 'hash1')
    # None of these should raise
    logger.info('test info')
    logger.warning('test warning')
    logger.error('test error')
    logger.debug('test debug')
