import os
import io
import base64
from pypdf import PdfReader
from core.settings import settings


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_poppler_path():
    p = settings.get('pdf:poppler_path')
    return str(p) if p and os.path.isdir(str(p)) else None


def _render_pdf_pages(file_path):
    """Render all PDF pages as 300-DPI PIL images via pdf2image/poppler."""
    try:
        from pdf2image import convert_from_path
        kwargs = {'dpi': 300}
        poppler = _get_poppler_path()
        if poppler:
            kwargs['poppler_path'] = poppler
        return convert_from_path(file_path, **kwargs)
    except Exception as e:
        print(f"  [pdf] render failed: {e}")
        return []


def _tesseract_ocr(pil_image):
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
        print(f"      [tess] {e}")
        return ''


def _vision_ocr(pil_image):
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
        )
        return response['message']['content'].strip()
    except Exception as e:
        print(f"      [vision] {e}")
        return ''


# ── main extractor ────────────────────────────────────────────────────────────

def extract(file_path: str) -> tuple:
    """
    Multi-stage PDF text extractor:
      1. pypdf  — fast native text layer
      2. pdf2image + Tesseract (psm 1/3) — for scanned / rotated pages
      3. Ollama vision model  — fallback when Tesseract returns nothing
    """
    print(f"  [pdf] Extracting: {os.path.basename(file_path)}")
    try:
        reader = PdfReader(file_path)
        if reader.is_encrypted:
            from pypdf import PasswordType
            if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                return None, "PDF is password-protected and could not be decrypted"

        threshold = int(settings.get('pdf:sparse_threshold') or 50)
        page_texts = []
        sparse_indices = []

        # Phase 1: native text layer
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ''
            page_texts.append(text)
            if len(text.strip()) < threshold:
                sparse_indices.append(i)

        # Phases 2 & 3: OCR sparse / empty pages
        if sparse_indices:
            print(f"  [pdf] {len(sparse_indices)}/{len(reader.pages)} "
                  f"page(s) sparse — rendering for OCR")
            rendered = _render_pdf_pages(file_path)

            for i in sparse_indices:
                if i >= len(rendered):
                    continue
                img = rendered[i]

                tess = _tesseract_ocr(img)
                if tess:
                    print(f"      Page {i+1}: Tesseract → {len(tess)} chars")
                    page_texts[i] = tess
                else:
                    print(f"      Page {i+1}: Tesseract empty → vision model")
                    vis = _vision_ocr(img)
                    if vis:
                        print(f"      Page {i+1}: vision → {len(vis)} chars")
                        page_texts[i] = vis

        final_text = "\n\n".join(t for t in page_texts if t.strip())
        if not final_text:
            return None, "No text found in PDF after all extraction attempts"
        return final_text, None

    except Exception as e:
        return None, f"Failed to extract PDF: {e}"
