"""
[ MICROSOFT WORD EXTRACTION KERNEL ]
Dual-mode engine designed for maximum fidelity across modern and legacy Word formats.

PIPELINE:
1. XML Parsing (.docx): Primary tier. Utilizes 'python-docx' to navigate the 
   OpenXML document tree and extract structured text from paragraphs and tables.
2. COM Automation (.doc): Legacy tier. For binary documents, the system triggers 
   Windows COM Automation (pywin32) to physically automate a local Word instance 
   for high-fidelity content recovery.

REQUIRES: Microsoft Word (for .doc support), pywin32.
"""

MANIFEST = {
    "id": "com.microsoft.word.standard",
    "version": "1.0.0",
    "name": "Microsoft Word Extractor",
    "extensions": ["docx", "doc"],
    "requires": ["python-docx", "pywin32"]
}

__description__ = (
    "A comprehensive Microsoft Word engine supporting both modern XML (.docx) "
    "and legacy binary (.doc) formats. It features a dual-tier recovery strategy "
    "using native parsing and Windows COM automation for maximum accuracy."
)

import os
from docx import Document
from core import logger
from core.extractors.base import ExtractorContext


def _extract_doc_legacy(file_path: str) -> tuple:
    """Extract text from old binary .doc files via Word COM automation."""
    try:
        import pythoncom
        import win32com.client
        
        pythoncom.CoInitialize()
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        
        doc = word.Documents.Open(os.path.abspath(file_path))
        text = doc.Content.Text
        doc.Close()
        word.Quit()
        return text, None
    except Exception as e:
        return None, f"Legacy .doc extraction failed (Word may not be installed): {e}"


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Extract content from a Word file using native parsing or COM fallback.
    """
    logger.info(f"Extracting: {os.path.basename(file_path)}", ext="ms-word")
    try:
        ext = os.path.splitext(file_path)[1].lower()
        
        # Handle legacy .doc
        if ext == '.doc':
            return _extract_doc_legacy(file_path)

        # Handle modern .docx
        doc = Document(file_path)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return None, "No text found in document"
        return "\n\n".join(paragraphs), None
    except Exception as e:
        return None, f"Failed to extract Word document: {e}"
