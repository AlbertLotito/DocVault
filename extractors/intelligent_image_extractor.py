"""
[ INTELLIGENT IMAGE ANALYSIS KERNEL ]
The system's primary vision engine for standalone image files (JPG, PNG, WebP, BMP).

PIPELINE:
1. Multi-Modal Analysis (Ollama): Primary tier. Uses a Vision LLM to perform 
   simultaneous scene description and deep-text transcription. It 'understands' 
   the context of the image (e.g., identifying objects, moods, or handwriting).
2. Deterministic OCR (Tesseract): Fallback tier. If AI inference is disabled or 
   fails, the system executes raw Optical Character Recognition for high-speed 
   text recovery.

REQUIRES: Ollama (vision:model), Tesseract (tesseract:path).
"""

MANIFEST = {
    "id": "com.docvault.vision.standard",
    "version": "1.1.0",
    "name": "Intelligent Image Analyzer",
    "extensions": ["jpg", "jpeg", "png", "webp", "bmp", "tiff", "tif"],
    "requires": ["ollama", "pytesseract", "pillow"]
}

__description__ = (
    "A dual-mode vision engine that performs simultaneous scene description and "
    "text transcription. It leverages Multi-Modal LLMs for deep semantic understanding "
    "of image content, with a high-speed Tesseract OCR fallback for raw text recovery."
)

import os
import pytesseract
from PIL import Image
from core.settings import settings
from core import logger
from core.extractors.base import ExtractorContext
from extractors.vision import describe as vision_describe


def _configure_tesseract() -> None:
    tesseract_path = settings.get('tesseract:path')
    if tesseract_path and os.path.exists(str(tesseract_path)):
        pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache_dir() -> str:
    return str(settings.get('paths:cache_directory') or '.cache/extracted_images')


def _is_pdf_page_image(file_path: str) -> bool:
    """True for images the PDF image harvester cut out into the cache directory."""
    def norm(p):
        return os.path.normcase(os.path.normpath(p))
    cache = norm(_cache_dir())
    candidates = {norm(file_path)}
    if not os.path.isabs(file_path):
        candidates.add(norm(os.path.join(_PROJECT_ROOT, file_path)))
    roots = {cache} if os.path.isabs(cache) else {cache, norm(os.path.join(_PROJECT_ROOT, cache))}
    return any(c.startswith(r + os.sep) for c in candidates for r in roots)


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Extract content from an image file using Vision LLM + OCR fallback.

    PDF page images (cut out by the PDF image harvester) get Tesseract first:
    their parent PDF already holds the page text, and a vision description of a
    scanned page mostly repeats it. Only if OCR finds no text (likely an embedded
    photo) does the vision model run.
    """
    logger.info(f"Analyzing: {os.path.basename(file_path)}", ext="vision-ai")
    meta = {"vision_stage": "initialized"}
    try:
        image = Image.open(file_path)

        if _is_pdf_page_image(file_path):
            meta["pdf_page_image"] = True
            _configure_tesseract()
            ocr_text = pytesseract.image_to_string(image, lang='eng').strip()
            if ocr_text:
                meta["ocr_stage"] = "success"
                return ocr_text, None, meta

        # 1. Vision model (scene description + transcription)
        vision_text = vision_describe(image)
        if vision_text:
            logger.debug(f"vision → {len(vision_text)} chars (description + text)", ext="vision-ai")
            meta["vision_stage"] = "success"
            return vision_text, None, meta

        # 2. Tesseract fallback (raw OCR)
        meta["vision_stage"] = "empty_or_skipped"
        logger.debug("vision empty/skipped → falling back to Tesseract OCR", ext="vision-ai")
        _configure_tesseract()
        ocr_text = pytesseract.image_to_string(image, lang='eng').strip()
        if ocr_text:
            logger.debug(f"tesseract → {len(ocr_text)} chars", ext="vision-ai")
            meta["ocr_stage"] = "success"
            return ocr_text, None, meta

        return None, "No content detected in image after all analysis attempts", meta

    except Exception as e:
        return None, f"Image analysis failed: {e}", meta
