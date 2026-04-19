"""
[ SWF (SHOCKWAVE FLASH) EXTRACTION KERNEL ]
Extracts text from Flash-based document viewers (.swf) by harvesting
embedded images and running Tesseract OCR on each one.

PIPELINE:
1. Header Parse: reads SWF signature, version, compression (none/zlib/lzma).
2. Tag Walk: scans all SWF tags for embedded image types:
     - DefineBitsLossless  (tag 20) — RGB palette/truecolor (no alpha)
     - DefineBitsJPEG3     (tag 21) — JPEG + optional alpha mask
     - DefineBitsJPEG4     (tag 35) — JPEG4 + deblock parameter
     - DefineBitsLossless2 (tag 74) — RGBA palette/truecolor (with alpha)
3. Filter: discards images below the area threshold (UI chrome, icons).
4. OCR: runs Tesseract on each qualifying image, ordered by character ID
   (Flash assigns IDs sequentially, so page order is preserved).
5. Combine: joins page texts with separators.

REQUIRES: pillow, pytesseract, Tesseract binary
"""

MANIFEST = {
    "id": "com.docvault.flash.swf",
    "version": "1.0.0",
    "name": "SWF Flash Extractor",
    "extensions": ["swf"],
    "requires": ["pillow", "pytesseract"],
}

__description__ = (
    "Extracts text from Flash (.swf) document viewers by harvesting embedded "
    "images and running Tesseract OCR on each page. Designed for newspaper/magazine "
    "SWF flipbooks where content is rasterised. Filters out UI chrome by minimum area."
)

import io
import os
import struct
import zlib
from core import logger
from core.extractors.base import ExtractorContext


# ── SWF image tag IDs ────────────────────────────────────────────────────────

_TAG_BITS_LOSSLESS  = 20   # DefineBitsLossless:  char_id(2)+fmt(1)+w(2)+h(2)[+ct(1)]+zlib
_TAG_BITS_JPEG2     = 21   # DefineBitsJPEG2:     char_id(2) + raw JPEG (no alpha_offset)
_TAG_BITS_JPEG3     = 35   # DefineBitsJPEG3:     char_id(2)+alpha_offset(4)+JPEG+alpha_mask
_TAG_BITS_JPEG4     = 36   # DefineBitsJPEG4:     char_id(2)+alpha_offset(4)+deblock(2)+JPEG
_TAG_BITS_LOSSLESS2 = 74   # DefineBitsLossless2: same layout as tag 20 but RGBA palette


# ── Header / decompression ────────────────────────────────────────────────────

def _decompress(raw: bytes) -> tuple[bytes, int, int]:
    """
    Returns (body, swf_version, frame_count).
    body starts at the byte after the 8-byte standard header.
    Raises ValueError for unknown signatures.
    """
    sig = raw[:3]
    version = raw[3]

    if sig == b'FWS':
        body = raw[8:]
    elif sig == b'CWS':
        body = zlib.decompress(raw[8:])
    elif sig == b'ZWS':
        try:
            import lzma
            body = lzma.decompress(raw[12:])
        except ImportError:
            raise ValueError("lzma module required for ZWS-compressed SWF")
    else:
        raise ValueError(f"Not a valid SWF file (signature: {raw[:3]!r})")

    # Skip RECT + frame-rate to get frame_count
    nbits      = (body[0] >> 3) & 0x1F
    rect_bytes = (5 + nbits * 4 + 7) // 8
    frame_count = struct.unpack_from('<H', body, rect_bytes + 2)[0]
    body_start  = rect_bytes + 4

    return body, version, frame_count, body_start


# ── Tag walker ────────────────────────────────────────────────────────────────

def _walk_tags(body: bytes, start: int):
    """
    Yield (tag_type, tag_data) for every tag in the SWF body.
    Stops at the End tag (type 0) or end of data.
    """
    pos = start
    while pos < len(body) - 1:
        if pos + 2 > len(body):
            break
        code_len = struct.unpack_from('<H', body, pos)[0]
        tag_type = code_len >> 6
        length   = code_len & 0x3F
        pos += 2
        if length == 0x3F:
            if pos + 4 > len(body):
                break
            length = struct.unpack_from('<I', body, pos)[0]
            pos += 4
        data = body[pos:pos + length]
        pos += length
        if tag_type == 0:
            break
        yield tag_type, data


# ── Image decoders ────────────────────────────────────────────────────────────

def _decode_jpeg2(data: bytes) -> 'Image | None':
    """Decode DefineBitsJPEG2: char_id(2) + raw JPEG data."""
    try:
        from PIL import Image
        return Image.open(io.BytesIO(data[2:])).convert('RGB')
    except Exception:
        return None


def _decode_jpeg3(data: bytes, extra_header: int = 0) -> 'Image | None':
    """Decode DefineBitsJPEG3/4: char_id(2) + alpha_offset(4) [+ extra(N)] + JPEG + alpha."""
    try:
        from PIL import Image
        prefix      = 6 + extra_header
        alpha_offset = struct.unpack_from('<I', data, 2)[0]
        jpeg_bytes   = data[prefix : prefix + alpha_offset]
        return Image.open(io.BytesIO(jpeg_bytes)).convert('RGB')
    except Exception:
        return None


def _decode_lossless(data: bytes, has_alpha: bool) -> 'Image | None':
    """
    Decode DefineBitsLossless / DefineBitsLossless2.
    Layout: char_id(2) + format(1) + width(2) + height(2) [+ ct_size(1)] + zlib(pixels)
    """
    try:
        from PIL import Image
        fmt    = data[2]
        width  = struct.unpack_from('<H', data, 3)[0]
        height = struct.unpack_from('<H', data, 5)[0]
        if width == 0 or height == 0:
            return None

        if fmt == 3:
            # 8-bit color-mapped
            ct_size   = data[7] + 1
            raw       = zlib.decompress(data[8:])
            bpp       = 4 if has_alpha else 3
            pal_end   = ct_size * bpp
            palette   = raw[:pal_end]
            indices   = raw[pal_end:]
            stride    = (width + 3) & ~3  # 4-byte aligned rows
            pixels    = bytearray(width * height * 3)
            for y in range(height):
                for x in range(width):
                    idx = indices[y * stride + x]
                    base = idx * bpp
                    out  = (y * width + x) * 3
                    pixels[out]     = palette[base]
                    pixels[out + 1] = palette[base + 1]
                    pixels[out + 2] = palette[base + 2]
            return Image.frombytes('RGB', (width, height), bytes(pixels))

        elif fmt == 5:
            # 32-bit per pixel
            raw = zlib.decompress(data[7:])
            if has_alpha:
                # ARGB → RGB: skip A, take R G B
                pixels = bytearray(width * height * 3)
                for i in range(width * height):
                    src = i * 4
                    dst = i * 3
                    pixels[dst]     = raw[src + 1]
                    pixels[dst + 1] = raw[src + 2]
                    pixels[dst + 2] = raw[src + 3]
            else:
                # PIX24: reserved + R + G + B → RGB
                pixels = bytearray(width * height * 3)
                for i in range(width * height):
                    src = i * 4
                    dst = i * 3
                    pixels[dst]     = raw[src + 1]
                    pixels[dst + 1] = raw[src + 2]
                    pixels[dst + 2] = raw[src + 3]
            return Image.frombytes('RGB', (width, height), bytes(pixels))

    except Exception:
        return None


# ── OCR helper ────────────────────────────────────────────────────────────────

def _ocr(img) -> str:
    """Run Tesseract on a PIL image. Returns stripped text or empty string."""
    try:
        import pytesseract
        from core.settings import settings
        tp = settings.get('tesseract:path')
        if tp and os.path.exists(str(tp)):
            pytesseract.pytesseract.tesseract_cmd = str(tp)
        text = pytesseract.image_to_string(img, lang='eng', config='--oem 3 --psm 1')
        if text.strip():
            return text.strip()
        return pytesseract.image_to_string(img, lang='eng', config='--oem 3 --psm 3').strip()
    except Exception as e:
        logger.warn(f"OCR failed: {e}", ext="swf")
        return ''


# ── Main extract ──────────────────────────────────────────────────────────────

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    from core.settings import settings

    logger.info(f"Parsing SWF: {os.path.basename(file_path)}", ext="swf")

    min_area  = int(settings.get('swf:min_image_area')  or 160_000)   # ~400×400
    max_pages = int(settings.get('swf:max_pages')        or 200)

    try:
        with open(file_path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        return None, f"Cannot read file: {e}"

    try:
        body, version, frame_count, body_start = _decompress(raw)
    except ValueError as e:
        return None, str(e)

    logger.info(f"SWF v{version}, {frame_count} frame(s), {len(raw):,} bytes", ext="swf")

    # Collect (char_id, PIL image) from image tags
    images = []
    for tag_type, data in _walk_tags(body, body_start):
        if len(data) < 7:
            continue
        char_id = struct.unpack_from('<H', data, 0)[0]

        if tag_type == _TAG_BITS_JPEG2:
            img = _decode_jpeg2(data)
        elif tag_type == _TAG_BITS_JPEG3:
            img = _decode_jpeg3(data, extra_header=0)
        elif tag_type == _TAG_BITS_JPEG4:
            img = _decode_jpeg3(data, extra_header=2)  # 2-byte deblock param
        elif tag_type == _TAG_BITS_LOSSLESS:
            img = _decode_lossless(data, has_alpha=False)
        elif tag_type == _TAG_BITS_LOSSLESS2:
            img = _decode_lossless(data, has_alpha=True)
        else:
            continue

        if img is None:
            continue
        w, h = img.size
        if w * h < min_area:
            continue
        images.append((char_id, w * h, img))

    if not images:
        return None, "No qualifying images found in SWF (all below area threshold)"

    # Sort by char_id to preserve page order; cap at max_pages
    images.sort(key=lambda x: x[0])
    images = images[:max_pages]

    logger.info(f"OCR-ing {len(images)} image(s) (area >= {min_area:,} px)", ext="swf")

    pages = []
    for i, (char_id, area, img) in enumerate(images, 1):
        if ctx.cancel_token and ctx.cancel_token.is_set():
            break
        logger.info(f"  OCR page {i}/{len(images)} (char_id={char_id}, {img.size[0]}×{img.size[1]})", ext="swf")
        text = _ocr(img)
        if text:
            pages.append(f"[Page {i}]\n{text}")

    if not pages:
        return None, "OCR produced no text from SWF images"

    result = "\n\n".join(pages)
    logger.info(f"Extracted {len(pages)} page(s), {len(result):,} chars", ext="swf")
    return result, None
