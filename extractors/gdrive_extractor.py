"""
[ GOOGLE DRIVE EXTRACTION KERNEL ]
Bridge kernel for synchronizing and extracting content from Google Workspace stubs.

PIPELINE:
1. Stub Resolution: Reads local .gdoc, .gsheet, and .gslides files to retrieve 
   the remote Document ID.
2. Cloud Export: Authenticates via OAuth2 and triggers the Google Drive API 
   to export Workspace documents into standard OpenXML or PDF formats.
3. Kernel Handoff: Hands the exported bitstream to the appropriate internal 
   kernel (Word, Excel, or PowerPoint) for high-fidelity extraction.

REQUIRES: Google Drive API credentials, OAuth2 token.
"""

MANIFEST = {
    "id": "com.google.drive.bridge",
    "version": "1.0.0",
    "name": "Google Drive Bridge",
    "extensions": ["gdoc", "gsheet", "gslides", "gform", "gdraw"],
    "requires": ["google-api-python-client", "google-auth-oauthlib"]
}

__description__ = (
    "A specialized bridge kernel for Google Workspace documents. It handles "
    "stub resolution, OAuth2 authentication, and cloud-to-local export, "
    "seamlessly integrating remote documents into the local extraction pipeline."
)

import json
import os
import tempfile

from core.settings import settings
from core import logger
from core.extractors.base import ExtractorContext


# ── MIME type mappings ────────────────────────────────────────────────────────

# Google Workspace MIME types → export MIME + temp file suffix
_EXPORT_MAP = {
    'application/vnd.google-apps.document':     ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'),
    'application/vnd.google-apps.spreadsheet':  ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',        '.xlsx'),
    'application/vnd.google-apps.presentation': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', '.pptx'),
    'application/vnd.google-apps.form':         ('text/csv',  '.csv'),
    'application/vnd.google-apps.drawing':      ('application/pdf', '.pdf'),
}

# Which DocVault extractor handles each exported suffix
def _extractor_for(suffix: str):
    from extractors import (
        microsoft_word_extractor, microsoft_excel_extractor, microsoft_powerpoint_extractor, 
        plaintext_extractor, text_extractor, image_extractor
    )
    return {
        '.docx': [microsoft_word_extractor],
        '.xlsx': [microsoft_excel_extractor],
        '.pptx': [microsoft_powerpoint_extractor],
        '.csv':  [plaintext_extractor],
        '.pdf':  [text_extractor, image_extractor],
    }.get(suffix, [plaintext_extractor])


# ── OAuth helpers ─────────────────────────────────────────────────────────────

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


def _creds_path() -> str:
    return (settings.get('google:credentials_path') or '').strip()

def _token_path() -> str:
    p = (settings.get('google:token_path') or '').strip()
    if p:
        return p
    # Default: alongside credentials file
    cp = _creds_path()
    return os.path.join(os.path.dirname(cp), 'google_token.json') if cp else ''


def is_authorized() -> bool:
    """Return True if a saved token exists and appears valid."""
    tp = _token_path()
    return bool(tp and os.path.exists(tp))


def ensure_authorized() -> tuple:
    """
    Run the OAuth flow if no token exists.
    """
    cp = _creds_path()
    tp = _token_path()
    if not cp or not os.path.exists(cp):
        return False, f"Credentials file not found: {cp}"
    if not tp:
        return False, "google:token_path is not configured"
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(cp, SCOPES)
        creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        with open(tp, 'w') as f:
            f.write(creds.to_json())
        return True, ''
    except Exception as e:
        return False, str(e)


def _get_credentials():
    """Load saved credentials, refreshing if expired."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    tp = _token_path()
    creds = Credentials.from_authorized_user_file(tp, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tp, 'w') as f:
            f.write(creds.to_json())
    return creds


def _build_service():
    from googleapiclient.discovery import build
    return build('drive', 'v3', credentials=_get_credentials())


# ── Main extractor ────────────────────────────────────────────────────────────

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Extract content from a Google Drive stub file (.gdoc, .gsheet, etc.).
    """
    logger.info(f"Processing stub: {os.path.basename(file_path)}", ext="gdrive")

    if not is_authorized():
        return None, "Google Drive not authorised — use Utilities → Authorise Google Drive"

    # Read the stub
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            stub = json.load(f)
    except Exception as e:
        return None, f"Could not read stub file: {e}"

    doc_id = stub.get('doc_id')
    if not doc_id:
        return None, "Stub file has no doc_id field"

    # Get file metadata from Drive to find its MIME type
    try:
        service = _build_service()
        meta = service.files().get(fileId=doc_id, fields='id,name,mimeType').execute()
    except Exception as e:
        return None, f"Drive API error: {e}"

    mime_type = meta.get('mimeType', '')
    logger.info(f"Target: {meta.get('name')} ({mime_type})", ext="gdrive")

    if mime_type not in _EXPORT_MAP:
        return None, f"Unsupported Google Workspace type: {mime_type}"

    export_mime, suffix = _EXPORT_MAP[mime_type]

    # Export to a temp file
    tmp_path = None
    try:
        request = service.files().export_media(fileId=doc_id, mimeType=export_mime)
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(request.execute())
        tmp.close()
        tmp_path = tmp.name
        logger.debug(f"Exported to {suffix} ({os.path.getsize(tmp_path)} bytes)", ext="gdrive")

        # Run through the appropriate DocVault extractor(s)
        extractors = _extractor_for(suffix)
        combined_text = []
        errors = []

        for extractor in extractors:
            # We must use the module directly here as these are legacy extractors
            result, err = extractor.extract(tmp_path, ctx)
            if err:
                errors.append(f"[{getattr(extractor, '__name__', 'unknown')}] {err}")
            if isinstance(result, str) and result:
                combined_text.append(result)
            elif isinstance(result, list):
                # image_extractor returns list of dicts with descriptions
                for item in result:
                    desc = item.get('description')
                    if desc:
                        combined_text.append(f"[Image — page {item.get('page_num', '?')}]\n{desc}")

        text = "\n\n".join(combined_text) if combined_text else None
        err_str = " | ".join(errors) if errors else None

        if not text:
            return None, err_str or "No content extracted"
        return text, err_str

    except Exception as e:
        return None, f"Export/extraction failed: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
