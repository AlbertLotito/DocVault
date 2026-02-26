import os
from docx import Document


def extract(file_path: str) -> tuple:
    print(f"  [word] Extracting: {os.path.basename(file_path)}")
    try:
        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            return None, "No text found in document"
        return "\n\n".join(paragraphs), None
    except Exception as e:
        return None, f"Failed to extract Word document: {e}"
