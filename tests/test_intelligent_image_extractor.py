from unittest.mock import patch, MagicMock
from extractors import intelligent_image_extractor


@patch('extractors.intelligent_image_extractor.pytesseract.image_to_string')
@patch('extractors.intelligent_image_extractor.Image.open')
def test_extract_returns_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "Extracted OCR text"

    text, error, meta = intelligent_image_extractor.extract("fake.png")
    assert error is None
    assert "Extracted OCR text" in text
    assert meta["vision_stage"] == "empty_or_skipped"


@patch('extractors.intelligent_image_extractor.Image.open')
def test_extract_returns_error_on_failure(mock_open):
    mock_open.side_effect = Exception("Cannot open image")

    text, error, meta = intelligent_image_extractor.extract("fake.png")
    assert text is None
    assert "Cannot open image" in error


@patch('extractors.intelligent_image_extractor.pytesseract.image_to_string')
@patch('extractors.intelligent_image_extractor.Image.open')
def test_extract_returns_error_when_no_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "   \n  "  # whitespace only

    text, error, meta = intelligent_image_extractor.extract("fake.png")
    assert text is None
    assert error is not None
