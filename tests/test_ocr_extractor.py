from unittest.mock import patch, MagicMock
from extractors import ocr_extractor


@patch('extractors.ocr_extractor.pytesseract.image_to_string')
@patch('extractors.ocr_extractor.Image.open')
def test_extract_returns_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "Extracted OCR text"

    text, error = ocr_extractor.extract("fake.png")
    assert error is None
    assert "Extracted OCR text" in text


@patch('extractors.ocr_extractor.Image.open')
def test_extract_returns_error_on_failure(mock_open):
    mock_open.side_effect = Exception("Cannot open image")

    text, error = ocr_extractor.extract("fake.png")
    assert text is None
    assert "Cannot open image" in error


@patch('extractors.ocr_extractor.pytesseract.image_to_string')
@patch('extractors.ocr_extractor.Image.open')
def test_extract_returns_error_when_no_text(mock_open, mock_ocr):
    mock_open.return_value = MagicMock()
    mock_ocr.return_value = "   \n  "  # whitespace only

    text, error = ocr_extractor.extract("fake.png")
    assert text is None
    assert error is not None
