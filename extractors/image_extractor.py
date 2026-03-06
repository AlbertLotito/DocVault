"""
[ PDF IMAGE HARVESTING KERNEL ]
Specialized engine for surgical extraction of embedded binary assets from PDF streams.

PIPELINE:
1. Binary Extraction (pypdf): Identifies and isolates XObject image streams.
2. Asset Caching: Saves raw images as PNGs in the '.cache/extracted_images' directory.
   Files are namespaced by source hash to prevent document-to-document collisions.
3. Vision Analysis (vision.py): Passes extracted images to the Vision AI for 
   natural-language description (e.g., 'Chart showing revenue').

REQUIRES: Vision Model (vision:describe_images).
"""

MANIFEST = {
    "id": "com.docvault.pdf.images",
    "version": "1.0.0",
    "name": "PDF Image Harvester",
    "extensions": ["pdf"],
    "requires": ["pypdf", "ollama", "pillow"]
}

__description__ = (
    "Specialized engine for surgical extraction of embedded binary assets from PDF streams. "
    "Identifies XObject images, saves them as PNGs in the system cache, and generates "
    "natural-language descriptions using Vision AI to enable visual-content search."
)

import os
from pypdf import PdfReader
from extractors.vision import describe as vision_describe
from core import logger
from core.extractors.base import ExtractorContext


def _cache_dir() -> str:
    from core.settings import settings
    d = settings.get('paths:cache_directory') or '.cache/extracted_images'
    os.makedirs(d, exist_ok=True)
    return d


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Extracts all embedded images from a PDF and saves them as PNGs.
    """
    logger.info(f"Extracting images from: {os.path.basename(file_path)}", ext="image")
    try:
        import hashlib
        pdf_stem = os.path.splitext(os.path.basename(file_path))[0]
        # Use first 8 chars of path hash for a short, collision-free suffix
        path_hash = hashlib.md5(file_path.encode()).hexdigest()[:8]
        output_dir = os.path.join(_cache_dir(), f"{pdf_stem}_{path_hash}")

        reader = PdfReader(file_path)
        if reader.is_encrypted:
            from pypdf import PasswordType
            if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                return None, "PDF is password-protected and could not be decrypted"

        extracted = []
        for page_num, page in enumerate(reader.pages, start=1):
            if not page.images:
                continue

            os.makedirs(output_dir, exist_ok=True)

            for img_idx, img_obj in enumerate(page.images, start=1):
                filename = f"page_{page_num:03d}_img_{img_idx:03d}.png"
                out_path = os.path.normpath(os.path.join(output_dir, filename))
                img_obj.image.save(out_path, "PNG")
                width, height = img_obj.image.size
                logger.debug(f"Saved: {filename} ({width}x{height})", ext="image")

                # Describe the image while we still have it in memory
                description = vision_describe(img_obj.image)
                if description:
                    logger.debug(f"Described: {filename} → {len(description)} chars", ext="image")

                extracted.append({
                    "file_path":   out_path,
                    "page_num":    page_num,
                    "image_index": img_idx,
                    "width":       width,
                    "height":      height,
                    "description": description or None,
                })

        return extracted, None

    except Exception as e:
        return None, f"Failed to extract images: {e}"
