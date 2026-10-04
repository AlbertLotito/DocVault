"""A log line the console can't encode must never crash the caller.

core.logger guarded only OSError. On a cp1252 console, logging '→' (the PDF
extractor's "Tesseract → N chars") raised UnicodeEncodeError inside the
extractor, so OCR failed with "'charmap' codec can't encode character".
"""
import io
import sys

from core import logger


def _cp1252_stdout(monkeypatch):
    buf = io.BytesIO()
    monkeypatch.setattr(sys, 'stdout', io.TextIOWrapper(buf, encoding='cp1252'))
    monkeypatch.setattr(logger, 'is_debug_enabled', lambda: True)
    return buf


def test_unencodable_characters_do_not_raise(monkeypatch):
    buf = _cp1252_stdout(monkeypatch)
    logger.debug('Page 1: Tesseract → 412 chars — 文字 \U0001F600', ext='pdf')   # must not raise
    sys.stdout.flush()
    out = buf.getvalue().decode('cp1252')
    assert 'Page 1: Tesseract' in out and '412 chars' in out


def test_encodable_text_is_printed_unchanged(monkeypatch):
    buf = _cp1252_stdout(monkeypatch)
    logger.error('Café résumé failed', ext='x')
    sys.stdout.flush()
    assert 'Café résumé failed' in buf.getvalue().decode('cp1252')
