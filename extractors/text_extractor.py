"""
[ PDF TEXT EXTRACTION KERNEL ]
The system's primary multi-stage PDF engine designed for maximum text recovery.

PIPELINE:
1. Native Stream (pypdf): Fastest layer; extracts embedded digital text.
2. OCR (Tesseract): Triggered for pages below the 'pdf:sparse_threshold'. 
   Uses Poppler for 300-DPI rendering and handles auto-rotation (OSD).
3. Vision-LLM (Ollama): Final fallback for Tesseract failures. 
   Uses the configured vision model to transcribe complex layouts or handwriting.

REQUIRES: Poppler (pdf:poppler_path), Tesseract (tesseract:path), Ollama (vision:model).
"""

MANIFEST = {
    "id": "com.docvault.pdf.text",
    "version": "1.1.0",
    "name": "PDF Text Engine",
    "extensions": ["pdf"],
    "requires": ["pypdf", "pdf2image", "pytesseract"]
}

__description__ = (
    "The system's primary multi-stage PDF engine designed for maximum text recovery. "
    "Features a tiered pipeline: (1) Native Stream extraction for digital text, "
    "(2) Tesseract OCR for sparse or scanned pages with auto-rotation, and "
    "(3) Ollama Vision LLM as a final fallback for complex handwriting or layouts."
)

import os
import re
import io
import base64
from pypdf import PdfReader
from core.settings import settings
from core import logger
from core.extractors.base import ExtractorContext


# ── helpers ──────────────────────────────────────────────────────────────────

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_poppler_path() -> str | None:
    """Configured poppler bin dir; a relative path is relative to the project root
    (not the process cwd)."""
    p = settings.get('pdf:poppler_path')
    if not p:
        return None
    p = str(p)
    if not os.path.isabs(p):
        p = os.path.join(_PROJECT_ROOT, p)
    return p if os.path.exists(p) else None


def _render_pdf_page(file_path: str, page_no: int):
    """Render one PDF page (1-based) as a 300-DPI PIL image via pdf2image/poppler.

    One page at a time: rendering a whole long scan at 300 DPI (~25 MB/page)
    could take gigabytes of RAM. Returns None on failure.
    """
    try:
        from pdf2image import convert_from_path
        kwargs = {'dpi': 300, 'first_page': page_no, 'last_page': page_no}
        poppler = _get_poppler_path()
        if poppler:
            kwargs['poppler_path'] = poppler
        images = convert_from_path(file_path, **kwargs)
        return images[0] if images else None
    except Exception as e:
        logger.error(f"render failed (page {page_no}): {e}", ext="pdf")
        return None


# Text layers with a broken font encoding come out as control characters
# ('\x00\n\x01\n…'); keep newlines and tabs, drop the rest.
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _meaningful_chars(text: str) -> int:
    """Letters and digits in any script: what decides whether a page needs OCR."""
    return sum(c.isalnum() for c in text)


def _tesseract_ocr(pil_image) -> str:
    """OCR a PIL image with Tesseract. Tries auto-rotation (psm 1) first,
    falls back to standard segmentation (psm 3) if OSD data is missing."""
    try:
        import pytesseract
        tp = settings.get('tesseract:path')
        if tp and os.path.exists(str(tp)):
            pytesseract.pytesseract.tesseract_cmd = str(tp)
        # psm 1 = auto OSD (handles rotated pages)
        try:
            text = pytesseract.image_to_string(
                pil_image, lang='eng', config='--oem 3 --psm 1')
            if text.strip():
                return text.strip()
        except Exception:
            pass
        # psm 3 = fully automatic, no OSD (safer fallback)
        text = pytesseract.image_to_string(
            pil_image, lang='eng', config='--oem 3 --psm 3')
        return text.strip()
    except Exception as e:
        logger.debug(f"tesseract failed: {e}", ext="pdf")
        return ''


def _vision_ocr(pil_image) -> str:
    """Use an Ollama vision model to extract text when Tesseract comes up empty."""
    try:
        import ollama
        model = settings.get('vision:model') or 'minicpm-v'
        buf = io.BytesIO()
        pil_image.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': (
                    'Extract all text from this image exactly as it appears. '
                    'Output only the extracted text with no additional commentary.'
                ),
                'images': [b64],
            }],
            options={'temperature': 0},
            keep_alive=60,
        )
        return response['message']['content'].strip()
    except Exception as e:
        logger.debug(f"vision failed: {e}", ext="pdf")
        return ''


# ── main extractor ────────────────────────────────────────────────────────────

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Multi-stage PDF text extractor:
      1. pypdf  — fast native text layer
      2. pdf2image + Tesseract (psm 1/3) — for scanned / rotated pages
      3. Ollama vision model  — fallback when Tesseract returns nothing
    """
    logger.info(f"Extracting: {os.path.basename(file_path)}", ext="pdf")
    try:
        reader = PdfReader(file_path)
        if reader.is_encrypted:
            from pypdf import PasswordType
            if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                return None, "PDF is password-protected and could not be decrypted"

        threshold = int(settings.get('pdf:sparse_threshold') or 50)
        page_texts = []
        sparse_indices = []
        ocr_pages = set()   # 1-based page numbers processed via OCR or vision

        # Phase 1: native text layer
        for i, page in enumerate(reader.pages):
            raw = page.extract_text() or ''
            # pypdf can produce lone surrogates from malformed PDF encodings;
            # strip them before any downstream UTF-8 serialisation.
            text = raw.encode('utf-8', errors='ignore').decode('utf-8')
            text = _CONTROL_CHARS.sub('', text)
            if _meaningful_chars(text) < threshold:
                sparse_indices.append(i)
                text = text if _meaningful_chars(text) else ''
            page_texts.append(text)

        # Phases 2 & 3: OCR sparse / empty pages, rendered one at a time
        if sparse_indices:
            logger.info(f"{len(sparse_indices)}/{len(reader.pages)} page(s) sparse — rendering for OCR", ext="pdf")

            for i in sparse_indices:
                img = _render_pdf_page(file_path, i + 1)
                if img is None:
                    continue

                tess = _tesseract_ocr(img)
                if tess:
                    logger.debug(f"Page {i+1}: Tesseract → {len(tess)} chars", ext="pdf")
                    page_texts[i] = tess
                    ocr_pages.add(i + 1)
                else:
                    logger.debug(f"Page {i+1}: Tesseract empty → vision model", ext="pdf")
                    vis = _vision_ocr(img)
                    if vis:
                        logger.debug(f"Page {i+1}: vision → {len(vis)} chars", ext="pdf")
                        page_texts[i] = vis
                    ocr_pages.add(i + 1)   # mark regardless — page was sparse

        final_text = "\n\n".join(t for t in page_texts if t.strip())
        meta = {'ocr_pages': sorted(ocr_pages)}
        if not final_text:
            return None, "No text found in PDF after all extraction attempts", meta
        return final_text, None, meta

    except Exception as e:
        return None, f"Failed to extract PDF: {e}"
