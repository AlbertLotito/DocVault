"""
Image kernels: no invented people, no duplicate OCR.

- face_narrative used a prompt that presupposes people ("Analyze the people in
  this image…") on every image, so sheet music and typed pages got invented
  scenes (25,154 stored texts). It now runs only when a face is detected.
- "No faces" is a normal outcome, not an error.
- PDF page images (cut out by the PDF image harvester into the extracted-images
  cache) get OCR only: their parent PDF already has the page text, and a vision
  description of a scanned page mostly repeats it. Those OCR-only, face-less
  page images don't write back into the parent either (that would duplicate it).
"""
import os
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from core import face_detect


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(vault_id='t', file_hash='t', cancel_token=threading.Event(),
                            logger=MagicMock(spec=ExtractorLogger), settings=MagicMock())


@pytest.fixture
def blank_png(tmp_path):
    p = tmp_path / 'sheet_music.png'
    Image.new('RGB', (400, 300), 'white').save(p)
    return str(p)


# ── shared face detector ─────────────────────────────────────────────────────

def test_blank_image_has_no_faces(blank_png):
    assert face_detect.count_faces(blank_png) == 0


def test_unreadable_image_counts_as_no_faces(tmp_path):
    bad = tmp_path / 'x.png'; bad.write_bytes(b'not an image')
    assert face_detect.count_faces(str(bad)) == 0


# ── narrative kernel ─────────────────────────────────────────────────────────

def test_narrative_is_skipped_without_faces(blank_png):
    from extractors import face_narrative_intelligence_extractor as fn
    with patch.object(fn, 'count_faces', return_value=0), patch.object(fn, 'vision_describe') as vd:
        text, error, meta = fn.extract(blank_png, _ctx())
    assert (text, error) == (None, None)
    assert meta['narrative_skipped'] == 'no faces'
    vd.assert_not_called()


def test_narrative_runs_when_faces_are_present(blank_png):
    from extractors import face_narrative_intelligence_extractor as fn
    with patch.object(fn, 'count_faces', return_value=2), \
         patch.object(fn, 'vision_describe', return_value='Two friends at a picnic.'):
        text, error, meta = fn.extract(blank_png, _ctx())
    assert error is None and 'Two friends at a picnic.' in text
    assert meta['narrative_success'] is True


# ── "no faces" is not an error ───────────────────────────────────────────────

def test_identity_kernel_no_faces_is_not_an_error(blank_png):
    from extractors import face_identity_extractor as fi
    text, error, meta = fi.extract(blank_png, _ctx())
    assert (text, error) == (None, None) and meta['faces_found'] == 0


def test_analytics_kernel_no_faces_is_not_an_error(blank_png):
    from extractors import face_analytics_extractor as fa
    result = fa.extract(blank_png, _ctx())
    assert result[0] is None and result[1] is None


# ── general image kernel: PDF page images get OCR only ───────────────────────

def test_pdf_page_image_gets_ocr_only(tmp_path, monkeypatch):
    from extractors import intelligent_image_extractor as ii
    cache = tmp_path / 'cache' / 'extracted_images' / 'Some Scan_abcd1234'
    cache.mkdir(parents=True)
    page = cache / 'page_001_img_001.png'
    Image.new('RGB', (400, 300), 'white').save(page)
    monkeypatch.setattr(ii, '_cache_dir', lambda: str(tmp_path / 'cache' / 'extracted_images'))
    with patch.object(ii, 'vision_describe') as vd, \
         patch.object(ii.pytesseract, 'image_to_string', return_value='Page one of the lease'):
        text, error, meta = ii.extract(str(page), _ctx())
    vd.assert_not_called()
    assert text == 'Page one of the lease'
    assert meta['pdf_page_image'] is True


def test_standalone_photo_still_gets_a_vision_description(blank_png, monkeypatch):
    from extractors import intelligent_image_extractor as ii
    monkeypatch.setattr(ii, '_cache_dir', lambda: r'Z:\nowhere\extracted_images')
    with patch.object(ii, 'vision_describe', return_value='A sunset over the lake.'):
        text, error, meta = ii.extract(blank_png, _ctx())
    assert text == 'A sunset over the lake.'
    assert not meta.get('pdf_page_image')


def test_relative_cache_path_is_recognised(monkeypatch):
    from extractors import intelligent_image_extractor as ii
    monkeypatch.setattr(ii, '_cache_dir', lambda: '.cache/extracted_images')
    assert ii._is_pdf_page_image(os.path.join('.cache', 'extracted_images', 'Doc_1', 'page_002_img_001.png'))
    assert not ii._is_pdf_page_image(r'Z:\Documents\Photos\beach.png')


# ── worker write-back ────────────────────────────────────────────────────────

def _child_task():
    return {'file_hash': 'c1', 'file_path': '.cache/extracted_images/D_1/page_001_img_001.png',
            'file_type': 'png', 'parent_hash': 'p1', 'metadata_json': '{"page_num": 1, "image_index": 1}'}


@patch('workers.extraction_worker.manager.append_parent_text')
@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_ocr_only_page_image_without_faces_does_not_write_back(mock_router, mock_complete, mock_append):
    from workers import extraction_worker
    k = MagicMock(); k.__name__ = 'intelligent_image_extractor'
    k.extract.return_value = ('Page one of the lease', None, {'pdf_page_image': True})
    mock_router.return_value = [k]
    extraction_worker.process_task('t.db', _child_task())
    mock_append.assert_not_called()


@patch('workers.extraction_worker.manager.reset_to_extracted_if_complete')
@patch('workers.extraction_worker.manager.append_parent_text')
@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_page_image_with_faces_still_writes_back(mock_router, mock_complete, mock_append, _reset):
    from workers import extraction_worker
    k = MagicMock(); k.__name__ = 'face_identity_extractor'
    k.extract.return_value = ('Face 1: adult', None, {'pdf_page_image': True, 'faces_found': 1})
    mock_router.return_value = [k]
    extraction_worker.process_task('t.db', _child_task())
    mock_append.assert_called_once()
