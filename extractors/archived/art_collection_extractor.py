"""
[ ART COLLECTION INTELLIGENCE KERNEL ]
Folder-level engine for art collection indexing and enrichment orchestration.

PIPELINE:
1. Collection Identification: Uses the folder name as the collection identifier.
2. Image Inventory: Scans the immediate folder for all image files, counting
   by format and listing filenames.
3. Enrichment Signalling: Records which images are awaiting Tier 3 enrichment
   (Google Vision identification + .nfo sidecar + rename).

REQUIRES: Nothing. Fast and dependency-free.
"""

MANIFEST = {
    "id": "com.docvault.art.collection",
    "version": "1.0.0",
    "name": "Art Collection Intelligence",
    "extensions": ["jpg", "jpeg", "png", "bmp", "tiff", "tif", "webp", "gif"],
    "target_type": "folder",
    "requires": []
}

__description__ = (
    "Folder-level kernel that inventories an art collection, using the folder name "
    "as the collection identifier. Produces a structural index of all image files "
    "and their formats, enabling collection-level search and enrichment tracking."
)

import os
from core import logger
from core.extractors.base import ExtractorContext

_IMAGE_EXTS = frozenset({
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp', '.gif'
})


def extract(dir_path: str, ctx: ExtractorContext) -> tuple:
    """
    Inventory an art collection folder and produce a structured summary.
    """
    collection_name = os.path.basename(os.path.normpath(dir_path))
    logger.info(f"Cataloguing collection: {collection_name}", ext="art")

    # Scan immediate folder only (non-recursive by design)
    try:
        entries = os.listdir(dir_path)
    except PermissionError as e:
        return None, f"Cannot read folder: {e}", {}

    image_files = sorted([
        f for f in entries
        if os.path.isfile(os.path.join(dir_path, f))
        and os.path.splitext(f)[1].lower() in _IMAGE_EXTS
    ])

    if not image_files:
        return None, "No image files found in folder", {}

    # Count by format
    fmt_counts = {}
    for f in image_files:
        ext = os.path.splitext(f)[1].lstrip('.').lower()
        fmt_counts[ext] = fmt_counts.get(ext, 0) + 1

    # Count .nfo sidecars to track enrichment progress
    nfo_files = {
        os.path.splitext(f)[0]
        for f in entries
        if f.lower().endswith('.nfo')
    }
    enriched = sum(1 for f in image_files if f in nfo_files)
    pending  = len(image_files) - enriched

    # Format string: "jpg (44), png (3)"
    fmt_str = ', '.join(
        f"{ext} ({count})"
        for ext, count in sorted(fmt_counts.items(), key=lambda x: -x[1])
    )

    report = [
        "[ ART COLLECTION ]",
        f"Collection: {collection_name}",
        f"Images: {len(image_files)}",
        f"Formats: {fmt_str}",
        f"Enriched: {enriched} / {len(image_files)}",
        "",
        "[ FILE LIST ]",
    ]
    report.extend(image_files)

    meta = {
        "collection":   collection_name,
        "image_count":  len(image_files),
        "formats":      fmt_counts,
        "enriched":     enriched,
        "pending_enrichment": pending,
    }

    return "\n".join(report), None, meta
