import os, tempfile, threading, pytest
from unittest.mock import MagicMock
from extractors import plaintext_extractor


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='test', file_hash='test',
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


def test_extracts_txt_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello world", encoding='utf-8')
    text, error = plaintext_extractor.extract(str(f), _ctx())
    assert error is None
    assert "Hello world" in text


def test_extracts_markdown(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Title\n\nSome content.", encoding='utf-8')
    text, error = plaintext_extractor.extract(str(f), _ctx())
    assert error is None
    assert "Title" in text


def test_returns_error_on_missing_file():
    text, error = plaintext_extractor.extract("/nonexistent/file.txt", _ctx())
    assert text is None
    assert error is not None


def test_handles_binary_looking_file(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("col1,col2\n1,2\n3,4", encoding='utf-8')
    text, error = plaintext_extractor.extract(str(f), _ctx())
    assert error is None
    assert "col1" in text
