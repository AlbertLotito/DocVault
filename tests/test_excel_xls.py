"""Legacy .xls workbooks (BIFF) — openpyxl only reads .xlsx, so 74 library
.xls files failed with "openpyxl does not support the old .xls file format"."""
import os
import threading
from unittest.mock import MagicMock

from extractors import microsoft_excel_extractor

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'sample.xls')


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(vault_id='t', file_hash='t', cancel_token=threading.Event(),
                            logger=MagicMock(spec=ExtractorLogger), settings=MagicMock())


def test_xls_extracts_every_sheet_as_tsv():
    text, error = microsoft_excel_extractor.extract(FIXTURE, _ctx())
    assert error is None
    assert '[Sheet: Accounts]' in text and '[Sheet: Notes]' in text
    assert '[Sheet: Empty]' not in text
    assert 'Card\tLimit\tOpened\tRate' in text
    assert 'Café receipts kept in drawer' in text


def test_xls_numbers_and_dates_read_like_xlsx():
    text, _ = microsoft_excel_extractor.extract(FIXTURE, _ctx())
    assert 'Visa\t5000\t2009-03-14\t0.1999' in text      # whole numbers without .0; dates as dates
    assert 'Amex\t12000\t2011-11-02' in text


def test_corrupt_xls_is_a_readable_error(tmp_path):
    bad = tmp_path / 'broken.xls'
    bad.write_bytes(bytes(range(256)) * 8)   # binary junk: neither a workbook nor a text export
    text, error = microsoft_excel_extractor.extract(str(bad), _ctx())
    assert text is None
    assert error.startswith('Failed to extract Excel file')


def test_tab_separated_text_saved_as_xls(tmp_path):
    """Old exports often wrote TSV/CSV text with an .xls name; Excel opens them fine."""
    f = tmp_path / 'export.xls'
    f.write_bytes(b'"DUNSNumber"\t"Company"\r\n"123"\t"Acme Widgets"\r\n')
    text, error = microsoft_excel_extractor.extract(str(f), _ctx())
    assert error is None
    assert 'Acme Widgets' in text


def test_html_workbook_saved_as_xls(tmp_path):
    """Excel's 'Save as Web Page' writes HTML (often UTF-16) with an .xls name."""
    html = '<html xmlns:x="urn:schemas-microsoft-com:office:excel"><body><table>' \
           '<tr><td>Name</td><td>Grade</td></tr><tr><td>Justin</td><td>A</td></tr></table></body></html>'
    f = tmp_path / 'demographics.xls'
    f.write_bytes(html.encode('utf-16'))
    text, error = microsoft_excel_extractor.extract(str(f), _ctx())
    assert error is None
    assert 'Justin' in text and '<td>' not in text


def test_xlrd_warnings_do_not_go_to_stdout(capsys):
    microsoft_excel_extractor.extract(FIXTURE, _ctx())
    assert 'OLE2' not in capsys.readouterr().out
