import os
from pypdf import PdfReader


def extract(file_path: str) -> tuple:
    """
    Extracts all text from a PDF file.

    Returns:
        (str, None) on success.
        (None, str) on failure, where the second element is the error message.
    """
    print(f"  [text] Extracting text from: {os.path.basename(file_path)}")
    try:
        reader = PdfReader(file_path)
        if reader.is_encrypted:
            from pypdf import PasswordType
            result = reader.decrypt("")
            if result == PasswordType.NOT_DECRYPTED:
                return None, "PDF is password-protected and could not be decrypted"

        pages_text = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages_text.append(page_text)

        if not pages_text:
            return None, "No text found in PDF (may be image-only)"

        return "\n\n".join(pages_text), None

    except Exception as e:
        return None, f"Failed to extract text: {e}"
