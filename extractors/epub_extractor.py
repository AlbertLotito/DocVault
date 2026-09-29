"""
[ EPUB EXTRACTION KERNEL ]
Extracts plain text from EPUB ebooks, in reading order.

PIPELINE:
1. Open: parse the EPUB container via ebooklib
2. Walk spine: iterate chapters/documents in reading order (not file order)
3. Strip: convert each chapter's XHTML to plain text via lxml
4. Concatenate: join chapters with a blank line between them

REQUIRES: ebooklib (pip install ebooklib), lxml
"""

MANIFEST = {
    "id": "com.docvault.document.epub",
    "version": "1.1.0",
    "name": "EPUB Text Extractor",
    "extensions": ["epub"],
    "requires": ["ebooklib", "lxml"]
}

__description__ = (
    "Extracts plain text from EPUB ebooks by walking the spine in reading "
    "order and stripping markup from each chapter's XHTML content."
)

import os
import re
from core import logger
from core.extractors.base import ExtractorContext

# Icon-font ligatures used for decorative bullets/quotes in some EPUB
# conversions land in the Unicode Private Use Area (codepoints 0xE000 to
# 0xF8FF) -- strip as noise. Built via chr() to keep this source file ASCII.
_PUA_LOW = chr(0xE000)
_PUA_HIGH = chr(0xF8FF)
_PUA_RE = re.compile('[' + _PUA_LOW + '-' + _PUA_HIGH + ']')


def _reading_order(book, document_type) -> list:
    """Spine documents in reading order. Manifest order is only a fallback for
    books with no usable spine -- it can differ from reading order and it also
    contains the navigation (TOC) document."""
    items = []
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is not None and item.get_type() == document_type:
            items.append(item)
    return items or list(book.get_items_of_type(document_type))


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Reading EPUB: {os.path.basename(file_path)}", ext="epub")
    try:
        import ebooklib
        from ebooklib import epub
        from lxml import html as lxml_html
    except ImportError as e:
        return None, f"ebooklib/lxml not installed: {e}"

    try:
        book = epub.read_epub(file_path, options={'ignore_ncx': True})
    except Exception as e:
        return None, f"Could not open EPUB: {e}"

    chapters = []
    for item in _reading_order(book, ebooklib.ITEM_DOCUMENT):
        try:
            content = item.get_content()
            text = lxml_html.fromstring(content).text_content()
            text = _PUA_RE.sub('', text)
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            if text:
                chapters.append(text)
        except Exception as e:
            logger.warn(f"Skipping unreadable chapter in {os.path.basename(file_path)}: {e}", ext="epub")

    if not chapters:
        return None, "No text content extracted from EPUB"

    full_text = '\n\n'.join(chapters)

    meta = {}
    try:
        titles = book.get_metadata('DC', 'title')
        if titles:
            meta['title'] = titles[0][0]
        creators = book.get_metadata('DC', 'creator')
        if creators:
            meta['author'] = creators[0][0]
    except Exception:
        pass

    if meta:
        return full_text, None, meta
    return full_text, None
