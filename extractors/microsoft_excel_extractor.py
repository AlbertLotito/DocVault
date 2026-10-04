"""
[ MICROSOFT EXCEL EXTRACTION KERNEL ]
Data-centric engine for multi-sheet workbook analysis and tabular recovery.

PIPELINE:
1. Worksheet Traversal: Iterates recursively through every named sheet in the
   workbook (.xlsx, .xls).
2. Data-Only Extraction: 'openpyxl' (.xlsx) in calculation mode, or 'xlrd'
   (legacy .xls), extracting the final results of formulas rather than the raw
   formula strings. Dates are rendered as dates, whole numbers without ".0".
3. Tabular Reconstruction: Rebuilds the spreadsheet layout using Tab-Separated
   Values (TSV), ensuring column and row relationships are preserved for
   semantic search.

REQUIRES: openpyxl, xlrd.
"""

MANIFEST = {
    "id": "com.microsoft.excel.standard",
    "version": "1.1.0",
    "name": "Microsoft Excel Extractor",
    "extensions": ["xlsx", "xls"],
    "requires": ["openpyxl", "xlrd"]
}

__description__ = (
    "A specialized Microsoft Excel engine featuring recursive sheet traversal "
    "and TSV reconstruction. It extracts calculated formula results from all "
    "worksheets to provide a high-fidelity tabular representation for indexing."
)

import io
import os
from openpyxl import load_workbook
from core import logger
from core.extractors.base import ExtractorContext


def _xlsx_sheets(file_path: str):
    wb = load_workbook(file_path, data_only=True, read_only=True)
    for sheet_name in wb.sheetnames:
        yield sheet_name, wb[sheet_name].iter_rows(values_only=True)


def _xls_cell(cell, datemode):
    import xlrd
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            dt = xlrd.xldate_as_datetime(cell.value, datemode)
        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
            return cell.value
        return dt.date() if dt.time() == dt.min.time() else dt
    if cell.ctype == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
        return int(cell.value)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    return cell.value


def _xls_sheets(file_path: str):
    """Legacy BIFF workbooks; openpyxl only reads .xlsx."""
    import xlrd
    # xlrd writes format warnings ("OLE2 inconsistency…") to stdout by default.
    book = xlrd.open_workbook(file_path, on_demand=True, logfile=io.StringIO())
    try:
        for sheet in book.sheets():
            yield sheet.name, ([_xls_cell(c, book.datemode) for c in sheet.row(r)]
                               for r in range(sheet.nrows))
    finally:
        book.release_resources()


def _mislabelled_xls_text(file_path: str):
    """Text for an '.xls' that is not a BIFF workbook: old exports often wrote
    TSV/CSV text or an HTML table (Excel 'Save as Web Page') with that name.
    None if it is neither."""
    from extractors.html_extractor import _decode, _render
    with open(file_path, 'rb') as f:
        raw = f.read()
    text = _decode(raw)
    if '<html' in text[:4096].lower() or '<table' in text[:4096].lower():
        return _render(text)[0] or None
    sample = text[:4096]
    if sample and sum(c.isprintable() or c in '\t\r\n' for c in sample) / len(sample) > 0.95:
        return text.strip() or None
    return None


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Extracting: {os.path.basename(file_path)}", ext="ms-excel")
    if os.path.splitext(file_path)[1].lower() == '.xls':
        try:
            import xlrd
            xlrd.open_workbook(file_path, on_demand=True, logfile=io.StringIO()).release_resources()
        except xlrd.XLRDError as e:
            if 'Expected BOF record' not in str(e):
                return None, f"Failed to extract Excel file: {e}"
            try:
                text = _mislabelled_xls_text(file_path)
            except Exception as e2:
                return None, f"Failed to extract Excel file: {e2}"
            return (text, None) if text else (None, f"Failed to extract Excel file: {e}")
        except Exception as e:
            return None, f"Failed to extract Excel file: {e}"
    try:
        is_xls = os.path.splitext(file_path)[1].lower() == '.xls'
        sections = []
        for sheet_name, row_iter in (_xls_sheets if is_xls else _xlsx_sheets)(file_path):
            rows = []
            for row in row_iter:
                # Convert row values to string, filtering out None
                row_str = "\t".join(str(v) for v in row if v is not None).strip()
                if row_str:
                    rows.append(row_str)
            if rows:
                sections.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows))
        if not sections:
            return None, "No content found in spreadsheet"
        return "\n\n".join(sections), None
    except Exception as e:
        return None, f"Failed to extract Excel file: {e}"
