import os
from pypdf import PdfReader
from extractors.vision import describe as vision_describe

# App root = two levels up from this file (extractors/ → app root)
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cache_dir() -> str:
    from core.settings import settings
    configured = (settings.get('paths:cache_directory') or '').strip()
    if configured:
        return configured
    return os.path.join(_APP_ROOT, '.cache', 'extracted_images')


def extract(file_path: str) -> tuple:
    """
    Extracts all embedded images from a PDF and saves them as PNGs.
    Images are written to the configured cache directory (not next to the PDF)
    in a subfolder named <pdf_stem>_<hash8> to avoid collisions.
    If the vision model is enabled, each image is also described and
    the description is stored in the 'description' field of the metadata dict
    (the worker collects these into the parent document's extracted text).

    Returns:
        (list[dict], None) on success.  Each dict has keys:
            file_path, page_num, image_index, width, height, description.
        Empty list if the PDF contains no embedded images.
        (None, str) on failure.
    """
    print(f"  [image] Extracting images from: {os.path.basename(file_path)}")
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
                print(f"    Saved: {filename} ({width}x{height})")

                # Describe the image while we still have it in memory
                description = vision_describe(img_obj.image)
                if description:
                    print(f"    Described: {filename} → {len(description)} chars")

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
