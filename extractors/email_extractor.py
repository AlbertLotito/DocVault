"""
[ EMAIL EXTRACTION KERNEL ]
Extracts searchable text and metadata from single email messages (RFC 822 / MIME).

Covers .eml and .wdseml (the per-message files Windows Search keeps for
Thunderbird mail, e.g. outlookdata\\ThunderbirdRoot).

PIPELINE:
1. Parse with the stdlib email package (policy.default): decodes RFC 2047
   encoded headers, quoted-printable/base64 bodies, and multipart structure.
2. Header block: From / To / Cc / Subject / Date, so a search for a sender or
   subject finds the message.
3. Body: the first text/plain part that is not an attachment; otherwise the
   first text/html part, rendered to plain text by the HTML kernel.
4. Attachments: names are listed (text + metadata); their contents are not
   extracted.

REQUIRES: Built-in Python libraries (+ lxml via the HTML kernel for HTML-only mail).
"""

MANIFEST = {
    "id": "com.docvault.document.email",
    "version": "1.0.0",
    "name": "Email Message Extractor",
    "extensions": ["eml", "wdseml"],
    "requires": []
}

__description__ = (
    "Extracts email messages (.eml, Thunderbird .wdseml): sender, recipients, "
    "subject and date as searchable text and metadata, the message body (plain "
    "text preferred, HTML rendered to text), and the names of attachments."
)

import email
import email.policy
import email.utils
import os
from core import logger
from core.extractors.base import ExtractorContext

_HEADER_FIELDS = (('From', 'from'), ('To', 'to'), ('Cc', 'cc'), ('Subject', 'subject'), ('Date', 'date'))
_IDENTIFYING_HEADERS = ('From', 'To', 'Subject', 'Date', 'Message-ID')


def _header(msg, name: str) -> str:
    try:
        value = msg[name]
    except Exception:          # malformed header the parser can't decode
        return ''
    return ' '.join(str(value).split()) if value is not None else ''


def _iso_date(raw: str) -> str:
    try:
        return email.utils.parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError, IndexError):
        return raw


def _is_attachment(part) -> bool:
    return part.get_content_disposition() == 'attachment'


def _part_text(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError, KeyError):
        from extractors.html_extractor import _decode
        return _decode(part.get_payload(decode=True) or b'', part.get_content_charset())


def _body(msg) -> str:
    plain = html = None
    for part in msg.walk():
        if part.is_multipart() or _is_attachment(part):
            continue
        ctype = part.get_content_type()
        if ctype == 'text/plain' and plain is None:
            plain = _part_text(part)
        elif ctype == 'text/html' and html is None:
            html = _part_text(part)
    if plain and plain.strip():
        return plain.strip()
    if html and html.strip():
        from extractors.html_extractor import _render
        text, _ = _render(html)
        return text
    return ''


def _attachments(msg) -> list:
    names = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename and (_is_attachment(part) or not part.get_content_type().startswith('text/')):
            names.append(' '.join(str(filename).split()))
    return names


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Reading email: {os.path.basename(file_path)}", ext="email")
    meta = {}
    try:
        with open(file_path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        return None, f"Could not read file: {e}", meta

    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        return None, f"Could not parse email: {e}", meta

    if not any(_header(msg, h) for h in _IDENTIFYING_HEADERS):
        return None, "Not an email message (no From/To/Subject/Date headers)", meta

    head = []
    for name, key in _HEADER_FIELDS:
        value = _header(msg, name)
        if value:
            head.append(f"{name}: {value}")
            meta[key] = _iso_date(value) if key == 'date' else value
    message_id = _header(msg, 'Message-ID')
    if message_id:
        meta['message_id'] = message_id

    try:
        body = _body(msg)
        attachments = _attachments(msg)
    except Exception as e:
        return None, f"Could not read email body: {e}", meta

    parts = ['\n'.join(head)]
    if body:
        parts.append(body)
    if attachments:
        meta['attachments'] = attachments
        parts.append('Attachments: ' + ', '.join(attachments))
    return '\n\n'.join(parts), None, meta
