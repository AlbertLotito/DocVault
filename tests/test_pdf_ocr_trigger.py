"""
When the PDF text extractor falls back to OCR.

- A page whose text layer is mostly control characters (broken font encoding,
  e.g. '\\x00\\n\\x01\\n\\x02…') was accepted as text and never OCR'd. Sparseness
  now counts meaningful (alphanumeric) characters, and control characters are
  stripped from text layers.
- Only the sparse pages are rendered for OCR, one at a time. Rendering every
  page at 300 DPI (about 25 MB each) risked ~10 GB of RAM on long scans.
"""
from unittest.mock import MagicMock

import pytest

from extractors import text_extractor as tx

GARBAGE = '\x00\n\x01\n\x02\n\x03\n\x04 \x07\n' + '\x08\n' * 300


class _Page:
    def __init__(self, text): self._t = text
    def extract_text(self): return self._t


@pytest.fixture
def pdf(monkeypatch):
    """Fake PdfReader whose pages have the given text layers; records OCR renders."""
    rendered = []

    def make(*page_texts, ocr=lambda n: f'OCR TEXT OF PAGE {n} with plenty of words'):
        reader = MagicMock(is_encrypted=False, pages=[_Page(t) for t in page_texts])
        monkeypatch.setattr(tx, 'PdfReader', lambda path: reader)
        monkeypatch.setattr(tx, '_render_pdf_page', lambda path, n: rendered.append(n) or f'img{n}')
        monkeypatch.setattr(tx, '_tesseract_ocr', lambda img: ocr(int(img[3:])))
        monkeypatch.setattr(tx, '_vision_ocr', lambda img: '')
        return rendered
    return make


def test_garbage_text_layer_falls_back_to_ocr(pdf):
    rendered = pdf(GARBAGE)
    text, error, meta = tx.extract('x.pdf', None)
    assert error is None
    assert rendered == [1]
    assert text == 'OCR TEXT OF PAGE 1 with plenty of words'
    assert meta['ocr_pages'] == [1]


def test_good_text_layer_is_not_ocrd(pdf):
    good = 'Savage Attacks. When you score a critical hit with a melee weapon attack, you roll again.'
    rendered = pdf(good)
    text, _, meta = tx.extract('x.pdf', None)
    assert rendered == []
    assert text == good and meta['ocr_pages'] == []


def test_only_sparse_pages_are_rendered(pdf):
    good = 'A perfectly normal paragraph of digital text that needs no OCR at all, thanks.'
    rendered = pdf(good, '', good, GARBAGE, good)
    text, _, meta = tx.extract('x.pdf', None)
    assert rendered == [2, 4]
    assert meta['ocr_pages'] == [2, 4]
    assert 'OCR TEXT OF PAGE 2' in text and 'OCR TEXT OF PAGE 4' in text


def test_control_characters_never_reach_the_index(pdf):
    pdf(GARBAGE, ocr=lambda n: '')                 # OCR and vision both find nothing
    text, error, _ = tx.extract('x.pdf', None)
    assert text is None
    assert 'No text found' in error


def test_non_latin_text_layer_counts_as_meaningful(pdf):
    thai = 'สัญญาจ้างลูกค้า ฉบับนี้ทำขึ้นระหว่างคู่สัญญาทั้งสองฝ่าย และมีผลบังคับใช้ทันที'
    rendered = pdf(thai)
    text, _, _ = tx.extract('x.pdf', None)
    assert rendered == [] and text == thai


def test_render_page_requests_a_single_page(monkeypatch):
    calls = []
    import sys, types
    fake = types.SimpleNamespace(convert_from_path=lambda path, **kw: calls.append(kw) or ['IMG'])
    monkeypatch.setitem(sys.modules, 'pdf2image', fake)
    assert tx._render_pdf_page('x.pdf', 7) == 'IMG'
    assert calls[0]['first_page'] == 7 and calls[0]['last_page'] == 7 and calls[0]['dpi'] == 300


def test_relative_poppler_path_resolves_against_the_project_root(monkeypatch, tmp_path):
    import os
    monkeypatch.chdir(tmp_path)                      # server cwd must not matter
    monkeypatch.setattr(tx.settings, 'get', lambda k: r'bin\poppler\Library\bin' if k == 'pdf:poppler_path' else None)
    p = tx._get_poppler_path()
    assert p and os.path.isabs(p) and os.path.isdir(p)
