"""
[ HTML EXTRACTION KERNEL ]
Extracts the readable text of web pages, as a person would see it in a browser.

PIPELINE:
1. Decode: honour a BOM or the page's declared charset (legacy Latin-1 pages are
   decoded as cp1252, as browsers do), else UTF-8, else cp1252.
   .mht/.mhtml (saved web pages) are MIME archives: the text/html part is used.
2. Parse: lxml's forgiving HTML parser (handles broken real-world markup).
3. Clean: capture <title> and meta description as metadata, then drop <head>,
   <script>, <style>, <noscript>, <template> and comments.
4. Render: collapse source whitespace as HTML does, break lines at block
   elements and <br>, decode entities, drop empty lines.

REQUIRES: lxml
"""

MANIFEST = {
    "id": "com.docvault.document.html",
    "version": "1.0.0",
    "name": "HTML Text Extractor",
    "extensions": ["html", "htm", "xhtml", "shtml", "mht", "mhtml"],
    "requires": ["lxml"]
}

__description__ = (
    "Extracts the readable text of web pages (HTML, XHTML, and saved .mht/.mhtml "
    "archives): strips markup, scripts, and styles, keeps paragraph and line "
    "structure, honours the declared encoding, and records the page title."
)

import email
import email.policy
import os
import re
from core import logger
from core.extractors.base import ExtractorContext

_MIME_ARCHIVE_EXTS = {'.mht', '.mhtml'}

# Elements that start a new line when rendered.
_BLOCK_TAGS = {
    'address', 'article', 'aside', 'blockquote', 'body', 'center', 'dd', 'details', 'dir',
    'div', 'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2',
    'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li', 'main', 'menu', 'nav', 'ol', 'p', 'pre',
    'section', 'summary', 'table', 'tbody', 'tfoot', 'thead', 'tr', 'ul', 'caption',
}
_CELL_TAGS = {'td', 'th'}
_DROP_TAGS = ('script', 'style', 'noscript', 'template', 'head')

# Paragraph separator as a line-break sentinel: it survives text_content() and
# can't be confused with source whitespace, which is collapsed first.
_NL = ' '
_WS = re.compile(r'\s+')
_CHARSET = re.compile(rb'charset\s*=\s*["\']?\s*([A-Za-z0-9_\-:.]+)', re.I)
_XML_DECL = re.compile(r'^\s*<\?xml[^>]*\?>', re.I)
_HEADER_LINE = re.compile(rb'[A-Za-z][A-Za-z0-9-]*:[ \t]')
# WHATWG: pages labelled Latin-1/ASCII are really decoded as windows-1252.
_CP1252_LABELS = {'iso-8859-1', 'iso8859-1', 'latin1', 'latin-1', 'us-ascii', 'ascii'}


def _decode(raw: bytes, declared: str | None = None) -> str:
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw[3:].decode('utf-8', errors='replace')
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16', errors='replace')
    if not declared:
        m = _CHARSET.search(raw[:4096])
        declared = m.group(1).decode('ascii', 'ignore') if m else None
    candidates = []
    if declared:
        d = declared.strip().lower()
        candidates.append('cp1252' if d in _CP1252_LABELS else d)
    candidates += ['utf-8', 'cp1252']
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode('cp1252', errors='replace')


def _looks_like_mime(raw: bytes) -> bool:
    """True for MIME messages saved under an .htm(l) name (old mail clients did this):
    the file opens with an RFC 822 header block that declares a Content-Type."""
    header_block = raw[:4096].lstrip().split(b'\n\n', 1)[0].split(b'\r\n\r\n', 1)[0]
    return bool(_HEADER_LINE.match(header_block)) and b'content-type:' in header_block.lower()


def _html_from_mime_archive(raw: bytes) -> tuple:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            payload = part.get_payload(decode=True) or b''
            return _decode(payload, part.get_content_charset()), None
    return None, "No HTML part found in MHT archive"


def _render(markup: str) -> tuple:
    """(text, meta) for an HTML document string."""
    from lxml import etree, html as lxml_html

    markup = _XML_DECL.sub('', markup, count=1)   # lxml rejects str input with an encoding declaration
    root = lxml_html.document_fromstring(markup)

    meta = {}
    title = root.find('.//title')
    if title is not None and title.text_content().strip():
        meta['title'] = _WS.sub(' ', title.text_content()).strip()
    for m in root.iter('meta'):
        if (m.get('name') or '').lower() == 'description' and (m.get('content') or '').strip():
            meta['description'] = _WS.sub(' ', m.get('content')).strip()
            break

    for el in list(root.iter(etree.Comment, etree.ProcessingInstruction)):
        el.drop_tree()
    for tag in _DROP_TAGS:
        for el in list(root.iter(tag)):
            el.drop_tree()

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        in_pre = el.tag == 'pre' or any(a.tag == 'pre' for a in el.iterancestors())
        if not in_pre:
            if el.text:
                el.text = _WS.sub(' ', el.text)
        if el.tail and not (el.getparent() is not None and el.getparent().tag == 'pre'):
            el.tail = _WS.sub(' ', el.tail)
        if el.tag == 'pre' and el.text:
            el.text = el.text.replace('\n', _NL)
        if el.tag in _BLOCK_TAGS:
            el.text = _NL + (el.text or '')
            el.tail = _NL + (el.tail or '')
        elif el.tag == 'br':
            el.tail = _NL + (el.tail or '')
        elif el.tag in _CELL_TAGS:
            el.tail = ' ' + (el.tail or '')

    lines = [line.strip() for line in root.text_content().split(_NL)]
    return '\n'.join(line for line in lines if line), meta


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Reading HTML: {os.path.basename(file_path)}", ext="html")
    meta = {}
    try:
        with open(file_path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        return None, f"Could not read file: {e}", meta

    if os.path.splitext(file_path)[1].lower() in _MIME_ARCHIVE_EXTS or _looks_like_mime(raw):
        markup, err = _html_from_mime_archive(raw)
        if err:
            return None, err, meta
        meta['format'] = 'mht'
    else:
        markup = _decode(raw)

    try:
        text, page_meta = _render(markup)
    except ImportError as e:
        return None, f"lxml not installed: {e}", meta
    except Exception as e:
        return None, f"Could not parse HTML: {e}", meta
    meta.update(page_meta)

    if not text:
        return None, "No visible text content in page", meta
    return text, None, meta
