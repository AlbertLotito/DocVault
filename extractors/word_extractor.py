import os
from docx import Document


def _extract_doc_legacy(file_path: str) -> tuple:
    """Extract text from old binary .doc files via Word COM automation."""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        word = win32com.client.Dispatch('Word.Application')
        word.Visible = False
        try:
            doc = word.Documents.Open(os.path.abspath(file_path), ReadOnly=True)
            text = doc.Content.Text.strip()
            doc.Close(False)
        finally:
            word.Quit()
            pythoncom.CoUninitialize()
        if not text:
            return None, "No text found in document"
        return text, None
    except ImportError:
        return None, "pywin32 not installed — cannot extract legacy .doc files"
    except Exception as e:
        return None, f"Failed to extract .doc via Word COM: {e}"


def extract(file_path: str) -> tuple:
    print(f"  [word] Extracting: {os.path.basename(file_path)}")
    if os.path.splitext(file_path)[1].lower() == '.doc':
        return _extract_doc_legacy(file_path)
    try:
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return None, "No text found in document"
        return "\n\n".join(paragraphs), None
    except Exception as e:
        return None, f"Failed to extract Word document: {e}"
