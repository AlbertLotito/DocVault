"""
[ PDF IMAGE HARVESTING KERNEL ]
Specialized engine for surgical extraction of embedded binary assets from PDF streams.

PIPELINE:
1. Filter Pass: deduplication (SHA-256), area threshold, per-PDF cap, OCR-page skip.
2. Asset Caching: Saves qualifying images as PNGs in the '.cache/extracted_images' dir,
   namespaced by source hash to prevent document-to-document collisions.
3. Child Task Dispatch: Each qualifying image is registered as a PENDING extraction task.
   Vision analysis runs asynchronously via the intelligent_image_extractor kernel.
   Results are written back to this PDF's record when child tasks complete.

No Ollama calls happen inline — this kernel returns in milliseconds regardless of PDF size.

REQUIRES: pypdf, pillow
"""

MANIFEST = {
    "id": "com.docvault.pdf.images",
    "version": "2.0.0",
    "name": "PDF Image Harvester",
    "extensions": ["pdf"],
    "requires": ["pypdf", "pillow"]
}

__description__ = (
    "Fast PDF image harvester. Extracts embedded images with deduplication, area filtering, "
    "and per-PDF cap. Dispatches each qualifying image as a child extraction task for "
    "asynchronous vision analysis. Descriptions are written back to the parent PDF record "
    "when child tasks complete."
)

import hashlib
import os
import json
from pypdf import PdfReader
from core import logger
from core.extractors.base import ExtractorContext, ChildTask


def _cache_dir() -> str:
    from core.settings import settings
    d = settings.get('paths:cache_directory') or '.cache/extracted_images'
    os.makedirs(d, exist_ok=True)
    return d


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Filter, save, and dispatch embedded PDF images as child tasks.
    Returns (images_list, error, metadata) — no vision calls inline.
    """
    from core.settings import settings
    from core import manager
    from api.main import DB_PATH

    logger.info(f"Harvesting images: {os.path.basename(file_path)}", ext="image")

    max_images = max(0, int(settings.get('pdf:max_images_per_pdf') or 50))
    min_area   = int(settings.get('pdf:min_image_area') or 10000)

    # Read ocr_pages from this task's metadata (written by text_extractor earlier)
    try:
        task_meta  = manager.get_task_metadata(DB_PATH, ctx.file_hash)
        ocr_pages  = set(task_meta.get('ocr_pages', []))
    except Exception:
        ocr_pages = set()

    pdf_stem  = os.path.splitext(os.path.basename(file_path))[0]
    path_hash = hashlib.md5(file_path.encode()).hexdigest()[:8]
    output_dir = os.path.join(_cache_dir(), f"{pdf_stem}_{path_hash}")

    try:
        reader = PdfReader(file_path)
        if reader.is_encrypted:
            from pypdf import PasswordType
            if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                return None, "PDF is password-protected and could not be decrypted"
    except Exception as e:
        return None, f"Failed to open PDF: {e}"

    seen_hashes  = set()
    spawned      = 0
    skipped_dup  = 0
    skipped_area = 0
    skipped_ocr  = 0
    extracted    = []
    child_tasks  = []

    for page_num, page in enumerate(reader.pages, start=1):
        if not page.images:
            continue

        # Page dimensions for full-page image detection (opt 4)
        try:
            mb = page.mediabox
            page_area = float(mb.width) * float(mb.height)
        except Exception:
            page_area = 0.0

        for img_idx, img_obj in enumerate(page.images, start=1):

            pil_img = img_obj.image
            # Normalise colour mode
            if pil_img.mode not in ('RGB', 'RGBA', 'L', 'P'):
                pil_img = pil_img.convert('RGB')

            width, height = pil_img.size

            # ── Filter 1: deduplication ───────────────────────────────────────
            img_bytes = pil_img.tobytes()
            img_hash  = hashlib.sha256(img_bytes).hexdigest()
            if img_hash in seen_hashes:
                skipped_dup += 1
                logger.debug(f"p{page_num}i{img_idx}: duplicate — skipped", ext="image")
                continue
            seen_hashes.add(img_hash)

            # ── Filter 2: area ────────────────────────────────────────────────
            if width * height < min_area:
                skipped_area += 1
                logger.debug(f"p{page_num}i{img_idx}: too small ({width}×{height}) — skipped", ext="image")
                continue

            # ── Filter 3: cap ─────────────────────────────────────────────────
            if max_images > 0 and spawned >= max_images:
                remaining = sum(len(p.images) for p in reader.pages[page_num - 1:])
                logger.info(f"Cap reached ({max_images}): skipped ~{remaining} remaining images", ext="image")
                break

            # ── Filter 4: skip full-page images on OCR'd pages ────────────────
            if page_num in ocr_pages and page_area > 0:
                img_area = width * height
                # pypdf page dimensions are in points; image pixels aren't directly
                # comparable, but if the image dominates the page it's a scanned page.
                # Use a pixel-count heuristic: if image is > 80% of likely page pixels
                # (assume 150dpi for a typical scanned page for the check)
                approx_page_px = page_area * (150 / 72) ** 2
                if approx_page_px > 0 and (img_area / approx_page_px) >= 0.8:
                    skipped_ocr += 1
                    logger.debug(f"p{page_num}i{img_idx}: full-page on OCR'd page — skipped", ext="image")
                    continue

            # ── Save PNG ──────────────────────────────────────────────────────
            os.makedirs(output_dir, exist_ok=True)
            filename = f"page_{page_num:03d}_img_{img_idx:03d}.png"
            out_path = os.path.normpath(os.path.join(output_dir, filename))
            pil_img.save(out_path, "PNG")

            # Compute file_hash for the saved PNG
            with open(out_path, 'rb') as f:
                png_hash = hashlib.sha256(f.read()).hexdigest()

            logger.debug(f"Saved: {filename} ({width}×{height})", ext="image")

            extracted.append({
                "file_path":   out_path,
                "page_num":    page_num,
                "image_index": img_idx,
                "width":       width,
                "height":      height,
                "description": None,   # filled in by child task write-back
            })

            child_tasks.append(ChildTask(
                file_path     = out_path,
                file_type     = 'png',
                vault_id      = ctx.vault_id or '',
                file_hash     = png_hash,
                priority      = 5,    # lower than normal extraction
                parent_hash   = ctx.file_hash,
                metadata_json = {
                    'parent_hash':  ctx.file_hash,
                    'parent_path':  file_path,
                    'page_num':     page_num,
                    'image_index':  img_idx,
                },
            ))
            spawned += 1

        else:
            # inner loop completed without break — continue outer loop
            continue
        break  # cap was hit — stop page iteration too

    logger.info(
        f"Harvested {spawned} image(s) for {os.path.basename(file_path)} "
        f"(dup={skipped_dup} area={skipped_area} ocr={skipped_ocr})",
        ext="image"
    )

    # Return 3-tuple so LegacyExtractorAdapter passes child_tasks through.
    # We attach child_tasks in metadata so the worker can dispatch them.
    # The worker reads result.child_tasks from IngestResult — but since we return
    # a list[dict] as the value, the adapter normalizes it into result.images.
    # We instead pass child_tasks via metadata key and let the worker handle dispatch.
    return extracted, None, {'_child_tasks': [
        {
            'file_path':     ct.file_path,
            'file_type':     ct.file_type,
            'vault_id':      ct.vault_id,
            'file_hash':     ct.file_hash,
            'priority':      ct.priority,
            'parent_hash':   ct.parent_hash,
            'metadata_json': ct.metadata_json,
        }
        for ct in child_tasks
    ]}
