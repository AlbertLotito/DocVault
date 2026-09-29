import threading
from unittest.mock import MagicMock, patch

from extractors import svg_extractor


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60">
  <rect width="120" height="60" fill="white"/>
  <rect x="10" y="10" width="40" height="40" fill="red"/>
</svg>"""


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='test', file_hash='test',
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


def _svg(tmp_path, content=SVG):
    f = tmp_path / 'pic.svg'
    f.write_text(content, encoding='utf-8')
    return str(f)


def test_rasterizes_at_300_dpi_like_pdf_ocr(tmp_path):
    # SVG px are 1/96 in, so 300 dpi is 3.125x the declared size.
    img = svg_extractor._rasterize(_svg(tmp_path))
    assert img.mode == 'RGB'
    assert abs(img.size[0] - 375) <= 1 and abs(img.size[1] - 188) <= 1
    r, g, _ = img.getpixel((90, 90))            # inside the red square
    assert r > 200 and g < 50


def test_small_icons_are_rendered_large_enough_for_vision(tmp_path):
    from extractors.vision import _check_image
    icon = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><circle cx="12" cy="12" r="10"/></svg>'
    img = svg_extractor._rasterize(_svg(tmp_path, icon))
    assert _check_image(img) == (True, '')


def test_huge_svgs_are_capped(tmp_path):
    huge = '<svg xmlns="http://www.w3.org/2000/svg" width="20000" height="5000"><rect width="20000" height="5000"/></svg>'
    img = svg_extractor._rasterize(_svg(tmp_path, huge))
    assert max(img.size) <= svg_extractor._MAX_RASTER_SIDE
    assert abs(img.size[0] / img.size[1] - 4.0) < 0.01


def test_vision_description_is_primary(tmp_path):
    with patch.object(svg_extractor, 'vision_describe', return_value='A red square.') as vd, \
         patch.object(svg_extractor.pytesseract, 'image_to_string') as ocr:
        text, error, meta = svg_extractor.extract(_svg(tmp_path), _ctx())

    assert (text, error) == ('A red square.', None)
    assert meta['vision_stage'] == 'success'
    vd.assert_called_once()
    ocr.assert_not_called()


def test_falls_back_to_ocr_when_vision_returns_nothing(tmp_path):
    with patch.object(svg_extractor, 'vision_describe', return_value=''), \
         patch.object(svg_extractor.pytesseract, 'image_to_string', return_value='  LABEL TEXT \n'):
        text, error, meta = svg_extractor.extract(_svg(tmp_path), _ctx())

    assert (text, error) == ('LABEL TEXT', None)
    assert meta['vision_stage'] == 'empty_or_skipped'
    assert meta['ocr_stage'] == 'success'


def test_no_content_from_either_tier_is_an_error(tmp_path):
    with patch.object(svg_extractor, 'vision_describe', return_value=''), \
         patch.object(svg_extractor.pytesseract, 'image_to_string', return_value='   '):
        text, error, _ = svg_extractor.extract(_svg(tmp_path), _ctx())

    assert text is None
    assert 'No content detected' in error


def test_malformed_svg_is_a_rasterization_error(tmp_path):
    with patch.object(svg_extractor, 'vision_describe') as vd:
        text, error, _ = svg_extractor.extract(_svg(tmp_path, '<svg><not closed'), _ctx())

    assert text is None
    assert 'SVG rasterization failed' in error
    vd.assert_not_called()


def test_analysis_failure_is_reported_not_raised(tmp_path):
    with patch.object(svg_extractor, 'vision_describe', side_effect=RuntimeError('ollama down')):
        text, error, _ = svg_extractor.extract(_svg(tmp_path), _ctx())

    assert text is None
    assert 'SVG analysis failed: ollama down' in error
