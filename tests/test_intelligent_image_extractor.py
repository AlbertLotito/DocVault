import threading
from unittest.mock import patch, MagicMock
from extractors import intelligent_image_extractor


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='test', file_hash='test',
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


@patch('extractors.intelligent_image_extractor.vision_describe', return_value='')
@patch('extractors.intelligent_image_extractor.pytesseract.image_to_string')
@patch('extractors.intelligent_image_extractor.Image.open')
def test_extract_returns_text(mock_open, mock_ocr, mock_vision):
    img = MagicMock()
    img.size = (800, 600)
    mock_open.return_value = img
    mock_ocr.return_value = "Extracted OCR text"

    text, error, meta = intelligent_image_extractor.extract("fake.png", _ctx())
    assert error is None
    assert "Extracted OCR text" in text
    assert meta["vision_stage"] == "empty_or_skipped"


@patch('extractors.intelligent_image_extractor.Image.open')
def test_extract_returns_error_on_failure(mock_open):
    mock_open.side_effect = Exception("Cannot open image")

    text, error, meta = intelligent_image_extractor.extract("fake.png", _ctx())
    assert text is None
    assert "Cannot open image" in error


@patch('extractors.intelligent_image_extractor.pytesseract.image_to_string')
@patch('extractors.intelligent_image_extractor.Image.open')
def test_extract_returns_error_when_no_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "   \n  "  # whitespace only

    text, error, meta = intelligent_image_extractor.extract("fake.png", _ctx())
    assert text is None
    assert error is not None
