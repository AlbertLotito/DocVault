"""
[ SVG EXTRACTION KERNEL ]
Extracts content from SVG vector images by rasterizing to a bitmap and
running the same vision pipeline used for standalone raster images.

PIPELINE:
1. Rasterize: parse SVG via svglib, render to PNG via reportlab at 300 dpi
   (longest side capped at 4000 px)
2. Vision model (scene description + transcription) — primary tier
3. Deterministic OCR (Tesseract) — fallback tier

REQUIRES: svglib, reportlab (pure Python, no system Cairo dependency),
          Ollama (vision:model), Tesseract (tesseract:path).
"""

MANIFEST = {
    "id": "com.docvault.vision.svg",
    "version": "1.1.0",
    "name": "SVG Vision Extractor",
    "extensions": ["svg"],
    "requires": ["svglib", "reportlab", "ollama", "pytesseract"]
}

__description__ = (
    "Rasterizes SVG vector images to a bitmap, then runs the same dual-mode "
    "vision pipeline (Vision LLM + Tesseract OCR fallback) used for standalone "
    "raster images."
)

import io
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


# Render at 300 dpi like the PDF OCR path so small icons clear vision's minimum
# size, but cap the longest side so a huge SVG can't exhaust memory.
_RASTER_DPI = 300
_MAX_RASTER_SIDE = 4000


def _rasterize(file_path: str) -> Image.Image:
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM

    drawing = svg2rlg(file_path)
    if drawing is None:
        raise ValueError("svglib could not parse this SVG")
    # svglib sizes drawings in points (1/72 in); renderPM draws at dpi/72 px per point.
    longest_pt = max(drawing.width, drawing.height, 1)
    dpi = min(_RASTER_DPI, _MAX_RASTER_SIDE * 72 / longest_pt)
    png_bytes = renderPM.drawToString(drawing, fmt='PNG', dpi=dpi)
    return Image.open(io.BytesIO(png_bytes)).convert('RGB')


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Analyzing SVG: {os.path.basename(file_path)}", ext="vision-svg")
    meta = {"vision_stage": "initialized"}
    try:
        image = _rasterize(file_path)
    except Exception as e:
        return None, f"SVG rasterization failed: {e}", meta

    try:
        vision_text = vision_describe(image)
        if vision_text:
            meta["vision_stage"] = "success"
            return vision_text, None, meta

        meta["vision_stage"] = "empty_or_skipped"
        _configure_tesseract()
        ocr_text = pytesseract.image_to_string(image, lang='eng').strip()
        if ocr_text:
            meta["ocr_stage"] = "success"
            return ocr_text, None, meta

        return None, "No content detected in SVG after all analysis attempts", meta

    except Exception as e:
        return None, f"SVG analysis failed: {e}", meta
