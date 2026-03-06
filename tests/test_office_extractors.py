import pytest
from unittest.mock import patch, MagicMock
from extractors import microsoft_word_extractor, microsoft_excel_extractor, microsoft_powerpoint_extractor


class TestWordExtractor:
    @patch('extractors.microsoft_word_extractor.Document')
    def test_extracts_paragraphs(self, mock_doc_class):
        mock_para = MagicMock()
        mock_para.text = "Hello from Word"
        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc_class.return_value = mock_doc

        text, error = microsoft_word_extractor.extract("fake.docx")
        assert error is None
        assert "Hello from Word" in text

    @patch('extractors.microsoft_word_extractor.Document')
    def test_returns_error_on_exception(self, mock_doc_class):
        mock_doc_class.side_effect = Exception("Corrupt file")
        text, error = microsoft_word_extractor.extract("fake.docx")
        assert text is None
        assert "Corrupt file" in error


class TestExcelExtractor:
    @patch('extractors.microsoft_excel_extractor.load_workbook')
    def test_extracts_cell_values(self, mock_load):
        mock_ws = MagicMock()
        mock_ws.title = "Sheet1"
        mock_ws.iter_rows.return_value = [
            [MagicMock(value="Name"), MagicMock(value="Age")],
            [MagicMock(value="Alice"), MagicMock(value=30)],
        ]
        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = lambda self, k: mock_ws
        mock_load.return_value = mock_wb

        text, error = microsoft_excel_extractor.extract("fake.xlsx")
        assert error is None
        assert "Name" in text
        assert "Alice" in text

    @patch('extractors.microsoft_excel_extractor.load_workbook')
    def test_returns_error_on_exception(self, mock_load):
        mock_load.side_effect = Exception("Bad file")
        text, error = microsoft_excel_extractor.extract("fake.xlsx")
        assert text is None
        assert error is not None


class TestPptxExtractor:
    @patch('extractors.microsoft_powerpoint_extractor.Presentation')
    def test_extracts_slide_text(self, mock_prs_class):
        mock_shape = MagicMock()
        mock_shape.has_text_frame = True
        mock_shape.text_frame.text = "Slide title"
        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]
        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]
        mock_prs_class.return_value = mock_prs

        text, error = microsoft_powerpoint_extractor.extract("fake.pptx")
        assert error is None
        assert "Slide title" in text

    @patch('extractors.microsoft_powerpoint_extractor.Presentation')
    def test_returns_error_on_exception(self, mock_prs_class):
        mock_prs_class.side_effect = Exception("Bad pptx")
        text, error = microsoft_powerpoint_extractor.extract("fake.pptx")
        assert text is None
        assert error is not None
