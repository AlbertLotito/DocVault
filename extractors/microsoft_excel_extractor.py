"""
[ MICROSOFT EXCEL EXTRACTION KERNEL ]
Data-centric engine for multi-sheet workbook analysis and tabular recovery.

PIPELINE:
1. Worksheet Traversal: Iterates recursively through every named sheet in the 
   workbook (.xlsx, .xls).
2. Data-Only Extraction: Utilizes 'openpyxl' in calculation mode to extract 
   the final results of formulas rather than the raw formula strings.
3. Tabular Reconstruction: Rebuilds the spreadsheet layout using Tab-Separated 
   Values (TSV), ensuring column and row relationships are preserved for 
   semantic search.

REQUIRES: openpyxl.
"""

__description__ = (
    "A specialized Microsoft Excel engine featuring recursive sheet traversal "
    "and TSV reconstruction. It extracts calculated formula results from all "
    "worksheets to provide a high-fidelity tabular representation for indexing."
)

import os
from openpyxl import load_workbook
from core import logger


def extract(file_path: str) -> tuple:
    logger.info(f"Extracting: {os.path.basename(file_path)}", ext="ms-excel")
    try:
        wb = load_workbook(file_path, data_only=True, read_only=True)
        sections = []
        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            rows = []
            for row in sheet.iter_rows(values_only=True):
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
