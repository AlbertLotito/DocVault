import os
from pypdf import PdfReader


def extract(file_path: str) -> tuple:
    """
    Extracts all embedded images from a PDF and saves them as PNGs in a
    sibling folder named after the PDF stem (without extension).

    Returns:
        (list[dict], None) on success. Each dict has keys:
            file_path, page_num, image_index, width, height.
        Empty list if the PDF contains no embedded images.
        (None, str) on failure.
    """
    print(f"  [image] Extracting images from: {os.path.basename(file_path)}")
    try:
        pdf_dir = os.path.dirname(file_path)
        pdf_stem = os.path.splitext(os.path.basename(file_path))[0]
        output_dir = os.path.join(pdf_dir, pdf_stem)

        reader = PdfReader(file_path)
        if reader.is_encrypted:
            from pypdf import PasswordType
            result = reader.decrypt("")
            if result == PasswordType.NOT_DECRYPTED:
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

                extracted.append({
                    "file_path": out_path,
                    "page_num": page_num,
                    "image_index": img_idx,
                    "width": width,
                    "height": height,
                })
                print(f"    Saved: {filename} ({width}x{height})")

        return extracted, None

    except Exception as e:
        return None, f"Failed to extract images: {e}"
