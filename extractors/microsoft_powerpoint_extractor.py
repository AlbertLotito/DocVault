"""
[ MICROSOFT POWERPOINT EXTRACTION KERNEL ]
Structural deconstruction engine for presentation-based documents.

PIPELINE:
1. Slide Traversal: Sequentially iterates through every slide object in the 
   presentation package (.pptx, .ppt).
2. Shape Tree Inspection: Walks the object hierarchy of each slide to 
   identify and isolate text frames (titles, bullets, and text boxes).
3. Narrative Reconstruction: Aggregates slide content while injecting 
   structural markers (e.g., [Slide 4]) to preserve the logical sequence and 
   context for the search engine.

REQUIRES: python-pptx.
"""

MANIFEST = {
    "id": "com.microsoft.powerpoint.standard",
    "version": "1.0.0",
    "name": "Microsoft PowerPoint Extractor",
    "extensions": ["pptx", "ppt"],
    "requires": ["python-pptx"]
}

__description__ = (
    "A specialized Microsoft PowerPoint engine that performs structural "
    "deconstruction of slide packages. It extracts and sequences text from titles, "
    "bullets, and shapes to reconstruct the presentation's narrative flow for indexing."
)

import os
from pptx import Presentation
from core import logger
from core.extractors.base import ExtractorContext


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Extracting: {os.path.basename(file_path)}", ext="ms-pptx")
    try:
        prs = Presentation(file_path)
        slides = []
        for i, slide in enumerate(prs.slides, start=1):
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        texts.append(t)
            if texts:
                slides.append(f"[Slide {i}]\n" + "\n".join(texts))
        if not slides:
            return None, "No text found in presentation"
        return "\n\n".join(slides), None
    except Exception as e:
        return None, f"Failed to extract PowerPoint file: {e}"
