"""
Gmail alert-channel client.

Mirrors extractors/gdrive_extractor.py's OAuth pattern, but scoped to
send-only Gmail access (gmail.send) via a dedicated token file -- kept
separate from the Drive Bridge's token so neither feature's authorization
requires touching the other's.
"""
import base64
import os
from email.mime.text import MIMEText

from core.settings import settings

SCOPES = ['https://www.googleapis.com/auth/gmail.send']


def _creds_path() -> str:
    return (settings.get('google:credentials_path') or '').strip()


def _token_path() -> str:
    p = (settings.get('gmail:token_path') or '').strip()
    if p:
        return p
    # Default: alongside credentials file
    cp = _creds_path()
    return os.path.join(os.path.dirname(cp), 'gmail_token.json') if cp else ''


def is_authorized() -> bool:
    """Return True if a saved token exists."""
    tp = _token_path()
    return bool(tp and os.path.exists(tp))


def ensure_authorized() -> tuple:
    """Run the OAuth flow if no token exists."""
    cp = _creds_path()
    tp = _token_path()
    if not cp or not os.path.exists(cp):
        return False, f"Credentials file not found: {cp}"
    if not tp:
        return False, "gmail:token_path is not configured"
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
    return build('gmail', 'v1', credentials=_get_credentials())


def send_email(subject: str, body: str) -> tuple:
    """Send a plain-text email via the Gmail API. Never raises."""
    to = (settings.get('alerts:email_to') or '').strip()
    if not to:
        return False, "alerts:email_to is not configured"
    if not is_authorized():
        return False, "Gmail is not authorized"
    try:
        message = MIMEText(body)
        message['to'] = to
        message['subject'] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        service = _build_service()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True, ''
    except Exception as e:
        return False, str(e)
