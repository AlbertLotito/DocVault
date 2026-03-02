import os
import pytesseract
from PIL import Image
from core.settings import settings
from extractors.vision import describe as vision_describe


def _configure_tesseract():
    tesseract_path = settings.get('tesseract:path')
    if tesseract_path and os.path.exists(str(tesseract_path)):
        pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)


def extract(file_path: str) -> tuple:
    """
    Extract content from an image file.
      1. Vision model — describes image and transcribes any text (primary)
      2. Tesseract OCR — fallback if vision is disabled or returns nothing
    """
    print(f"  [ocr] Processing: {os.path.basename(file_path)}")
    try:
        image = Image.open(file_path)

        # 1. Vision model (describe + transcribe)
        vision_text = vision_describe(image)
        if vision_text:
            print(f"    vision → {len(vision_text)} chars")
            return vision_text, None

        # 2. Tesseract fallback
        _configure_tesseract()
        ocr_text = pytesseract.image_to_string(image, lang='eng').strip()
        if ocr_text:
            print(f"    tesseract → {len(ocr_text)} chars")
            return ocr_text, None

        return None, "No content detected in image"

    except Exception as e:
        return None, f"OCR failed: {e}"
