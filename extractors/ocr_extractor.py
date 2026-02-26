import os
import pytesseract
from PIL import Image

# Tesseract path for Windows — adjust if installed elsewhere
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


def extract(file_path: str) -> tuple:
    """
    Runs Tesseract OCR on an image file and returns extracted text.
    """
    print(f"  [ocr] Processing: {os.path.basename(file_path)}")
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang='eng')
        text = text.strip()
        if not text:
            return None, "No text detected in image"
        return text, None
    except Exception as e:
        return None, f"OCR failed: {e}"
