# Gmail Alert Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add email as a third channel on DocVault's alert bus (`core/alerts.py`), delivered via the Gmail API, with a configurable minimum severity and recipient.

**Architecture:** A new `core/gmail_client.py` module mirrors `extractors/gdrive_extractor.py`'s existing OAuth pattern exactly (same `InstalledAppFlow` browser consent, same token-refresh logic, same `googleapiclient.discovery.build()` call shape) but scoped to `gmail.send` only, with its own dedicated token file. `core/alerts.py`'s `_dispatch_remote()` gains an email branch alongside its existing ntfy branch. Two new endpoints and a new Utils-page panel mirror the Drive Bridge's existing authorize UI exactly.

**Tech Stack:** `google-auth-oauthlib`, `googleapiclient` (both already installed — used by `extractors/gdrive_extractor.py`, just not previously imported by `core/`), Python stdlib `email.mime.text`/`base64`.

## Global Constraints

- No new pip dependency — `google-auth-oauthlib` and `googleapiclient` are already installed for the Drive Bridge extractor.
- Reuse the existing `google:credentials_path` setting for the OAuth client secrets file. Use a **new**, separate `gmail:token_path` setting — never add `gmail.send` to the Drive Bridge's existing token/scope.
- OAuth scope is exactly `https://www.googleapis.com/auth/gmail.send` — send-only.
- `alerts:email_min_level` default is `'error'`. An unrecognized value for this setting falls back to `'error'`'s rank.
- `alerts:email_to` default is `''` (empty). Empty disables the channel entirely — same convention as `alerts:ntfy_url`.
- The email channel must never raise or block `send_alert()` — every failure (not configured, not authorized, API error) is caught and logged via `logger.debug`, matching the existing ntfy branch's "fire and forget" contract exactly.
- Any new i18n key added to `frontend/static/i18n/en.json` must also be added (translated) to `es.json` and `fr.json` in the same task — `tests/test_i18n.py::test_es_and_fr_match_en_keys` checks all three files have matching key sets, and it must not gain new failures from this feature (it already has 3 unrelated missing keys from an earlier feature — out of scope to fix here, but must not grow).
- The real recipient address for manual end-to-end verification is `REDACTED-personal-email` — set via the Settings UI (or a one-time `settings.set()` call) after the feature is built, **never** hardcoded as the `alerts:email_to` schema default (that default must stay `''` — this is a personal address, not a code constant).

---

### Task 1: `core/gmail_client.py` — OAuth scaffolding + `send_email()`

**Files:**
- Create: `core/gmail_client.py`
- Test: `tests/test_gmail_client.py`

**Interfaces:**
- Produces: `SCOPES` (list, `['https://www.googleapis.com/auth/gmail.send']`), `is_authorized() -> bool`, `ensure_authorized() -> tuple[bool, str]`, `send_email(subject: str, body: str) -> tuple[bool, str]`. `is_authorized` and `send_email` are consumed by Task 3 (`core/alerts.py`) and Task 4 (`api/routes/utils.py`); `ensure_authorized` is consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gmail_client.py`:

```python
import os
import base64
from unittest.mock import patch, MagicMock

from core import gmail_client


def test_is_authorized_false_when_no_token(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'gmail:token_path': str(tmp_path / 'missing_token.json'),
    }.get(key, ''))
    assert gmail_client.is_authorized() is False


def test_is_authorized_true_when_token_exists(tmp_path, monkeypatch):
    token = tmp_path / 'token.json'
    token.write_text('{}')
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'gmail:token_path': str(token),
    }.get(key, ''))
    assert gmail_client.is_authorized() is True


def test_token_path_defaults_alongside_credentials(tmp_path, monkeypatch):
    creds = tmp_path / 'credentials.json'
    creds.write_text('{}')
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'google:credentials_path': str(creds),
        'gmail:token_path': '',
    }.get(key, ''))
    assert gmail_client._token_path() == os.path.join(str(tmp_path), 'gmail_token.json')


def test_ensure_authorized_fails_when_credentials_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'google:credentials_path': str(tmp_path / 'missing_creds.json'),
        'gmail:token_path': str(tmp_path / 'token.json'),
    }.get(key, ''))
    ok, err = gmail_client.ensure_authorized()
    assert ok is False
    assert 'Credentials file not found' in err


@patch('google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file')
def test_ensure_authorized_writes_token_on_success(mock_from_secrets, tmp_path, monkeypatch):
    creds_file = tmp_path / 'credentials.json'
    creds_file.write_text('{}')
    token_file = tmp_path / 'sub' / 'token.json'
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'google:credentials_path': str(creds_file),
        'gmail:token_path': str(token_file),
    }.get(key, ''))

    fake_creds = MagicMock()
    fake_creds.to_json.return_value = '{"token": "abc"}'
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = fake_creds
    mock_from_secrets.return_value = mock_flow

    ok, err = gmail_client.ensure_authorized()

    assert ok is True
    assert err == ''
    assert token_file.read_text() == '{"token": "abc"}'


def test_send_email_fails_when_recipient_not_configured(monkeypatch):
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: '')
    ok, err = gmail_client.send_email('subject', 'body')
    assert ok is False
    assert 'email_to' in err


def test_send_email_fails_when_not_authorized(monkeypatch):
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
    }.get(key, ''))
    monkeypatch.setattr(gmail_client, 'is_authorized', lambda: False)
    ok, err = gmail_client.send_email('subject', 'body')
    assert ok is False
    assert 'not authorized' in err.lower()


@patch('core.gmail_client._build_service')
def test_send_email_success_calls_gmail_api(mock_build_service, monkeypatch):
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
    }.get(key, ''))
    monkeypatch.setattr(gmail_client, 'is_authorized', lambda: True)

    mock_send = MagicMock()
    mock_service = MagicMock()
    mock_service.users.return_value.messages.return_value.send.return_value = mock_send
    mock_build_service.return_value = mock_service

    ok, err = gmail_client.send_email('Test Subject', 'Test Body')

    assert ok is True
    assert err == ''
    mock_send.execute.assert_called_once()
    call_kwargs = mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    assert call_kwargs['userId'] == 'me'
    raw = call_kwargs['body']['raw']
    decoded = base64.urlsafe_b64decode(raw).decode('utf-8')
    assert 'Test Subject' in decoded
    assert 'Test Body' in decoded


@patch('core.gmail_client._build_service')
def test_send_email_failure_returns_error_message(mock_build_service, monkeypatch):
    monkeypatch.setattr(gmail_client.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
    }.get(key, ''))
    monkeypatch.setattr(gmail_client, 'is_authorized', lambda: True)
    mock_build_service.side_effect = Exception("API quota exceeded")

    ok, err = gmail_client.send_email('subject', 'body')

    assert ok is False
    assert 'API quota exceeded' in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gmail_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.gmail_client'`.

- [ ] **Step 3: Implement `core/gmail_client.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gmail_client.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add core/gmail_client.py tests/test_gmail_client.py
git commit -m "feat: add Gmail OAuth client and send_email() for alert channel"
```

---

### Task 2: Settings schema additions

**Files:**
- Modify: `core/settings.py`
- Test: `tests/test_gmail_client.py` (append) or a new small test file — use `tests/test_settings_schema_gmail.py` for isolation

**Interfaces:**
- Produces: three new schema keys — `alerts:email_min_level` (default `'error'`), `alerts:email_to` (default `''`), `gmail:token_path` (default `''`). Consumed by Task 1 (already written against these keys) and Task 3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settings_schema_gmail.py`:

```python
from core.settings import SETTINGS_SCHEMA


def test_alerts_email_min_level_schema_entry():
    entry = SETTINGS_SCHEMA['alerts:email_min_level']
    assert entry['type'] == 'string'
    assert entry['default'] == 'error'


def test_alerts_email_to_schema_entry():
    entry = SETTINGS_SCHEMA['alerts:email_to']
    assert entry['type'] == 'string'
    assert entry['default'] == ''


def test_gmail_token_path_schema_entry():
    entry = SETTINGS_SCHEMA['gmail:token_path']
    assert entry['type'] == 'string'
    assert entry['default'] == ''
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings_schema_gmail.py -v`
Expected: FAIL with `KeyError: 'alerts:email_min_level'`.

- [ ] **Step 3: Implement in `core/settings.py`**

Find the existing `'alerts:ntfy_url'` entry (in the schema dict, in the "Alerts" section) and add the two new alert settings immediately after it:

```python
            'alerts:ntfy_url': {
                'type': 'string', 'default': '', 'label': 'ntfy.sh URL', 'group': 'general',
                'description': 'Optional: A ntfy.sh topic URL (e.g. https://ntfy.sh/my-private-topic) to receive system alerts on your phone or desktop.',
            },
            'alerts:email_min_level': {
                'type': 'string', 'default': 'error', 'label': 'Email alert minimum level', 'group': 'general',
                'description': 'Minimum alert severity that triggers an email (info, success, warning, error, or critical). Default is error -- only serious problems get emailed.',
            },
            'alerts:email_to': {
                'type': 'string', 'default': '', 'label': 'Alert email recipient', 'group': 'general',
                'description': 'Email address to receive alert emails. Leave empty to disable the email alert channel entirely.',
            },
```

Find the existing `'google:token_path'` entry (in the "Google Drive" section) and add the new Gmail token setting immediately after it:

```python
            'google:token_path': {
                'type': 'string', 'default': '', 'label': 'Token JSON path', 'group': 'google',
                'description': 'Path where the OAuth access token will be saved after the first authorisation. This file is created automatically when you authorise DocVault via the Utilities page. Keep it in the credentials folder alongside the credentials JSON.',
            },
            'gmail:token_path': {
                'type': 'string', 'default': '', 'label': 'Gmail alert token JSON path', 'group': 'google',
                'description': 'Path where the Gmail send-only OAuth token will be saved after authorising the alert-email channel via the Utilities page. Separate from the Google Drive token above -- authorising one does not authorise the other.',
            },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_settings_schema_gmail.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the Task 1 test file too, to confirm the schema additions don't affect it**

Run: `pytest tests/test_gmail_client.py -v`
Expected: still PASS (9 tests) -- Task 1's tests all monkeypatch `settings.get` directly and never depend on the real schema, so this is a safety check, not expected to reveal anything.

- [ ] **Step 6: Commit**

```bash
git add core/settings.py tests/test_settings_schema_gmail.py
git commit -m "feat: add alerts:email_min_level, alerts:email_to, gmail:token_path settings"
```

---

### Task 3: `core/alerts.py` — severity ranking + email dispatch branch

**Files:**
- Modify: `core/alerts.py`
- Test: `tests/test_alerts.py` (new)

**Interfaces:**
- Consumes: `gmail_client.is_authorized`, `gmail_client.send_email` (Task 1); `alerts:email_min_level`, `alerts:email_to` (Task 2).
- Produces: `_severity_rank(level: str, default: str = 'info') -> int`. Modifies `_get_ntfy_priority` (same public behavior, now implemented via `_severity_rank`) and `_dispatch_remote` (new email branch).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_alerts.py`:

```python
from unittest.mock import patch

from core import alerts


def test_severity_rank_orders_levels_correctly():
    assert (
        alerts._severity_rank('info')
        < alerts._severity_rank('success')
        < alerts._severity_rank('warning')
        < alerts._severity_rank('error')
        < alerts._severity_rank('critical')
    )


def test_severity_rank_unrecognized_level_uses_default():
    assert alerts._severity_rank('bogus', default='error') == alerts._severity_rank('error')
    assert alerts._severity_rank('bogus', default='info') == alerts._severity_rank('info')


def test_get_ntfy_priority_unchanged_behavior():
    assert alerts._get_ntfy_priority('critical') == '5'
    assert alerts._get_ntfy_priority('error') == '4'
    assert alerts._get_ntfy_priority('warning') == '3'
    assert alerts._get_ntfy_priority('success') == '2'
    assert alerts._get_ntfy_priority('info') == '1'
    assert alerts._get_ntfy_priority('bogus') == '3'


@patch('core.gmail_client.send_email')
@patch('core.gmail_client.is_authorized', return_value=True)
def test_dispatch_remote_sends_email_when_severity_clears_threshold(mock_is_auth, mock_send, monkeypatch):
    monkeypatch.setattr(alerts.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
        'alerts:email_min_level': 'error',
    }.get(key, ''))
    mock_send.return_value = (True, '')

    alerts.send_alert('Crash', 'Something broke', level='critical')

    mock_send.assert_called_once_with(subject='Crash', body='Something broke')


@patch('core.gmail_client.send_email')
@patch('core.gmail_client.is_authorized', return_value=True)
def test_dispatch_remote_skips_email_below_threshold(mock_is_auth, mock_send, monkeypatch):
    monkeypatch.setattr(alerts.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
        'alerts:email_min_level': 'error',
    }.get(key, ''))

    alerts.send_alert('FYI', 'Just letting you know', level='info')

    mock_send.assert_not_called()


def test_dispatch_remote_skips_email_when_recipient_not_configured(monkeypatch):
    monkeypatch.setattr(alerts.settings, 'get', lambda key: '')
    with patch('core.gmail_client.send_email') as mock_send:
        alerts.send_alert('Crash', 'Something broke', level='critical')
        mock_send.assert_not_called()


@patch('core.gmail_client.is_authorized', return_value=False)
def test_dispatch_remote_skips_email_when_not_authorized(mock_is_auth, monkeypatch):
    monkeypatch.setattr(alerts.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
        'alerts:email_min_level': 'error',
    }.get(key, ''))
    with patch('core.gmail_client.send_email') as mock_send:
        alerts.send_alert('Crash', 'Something broke', level='critical')
        mock_send.assert_not_called()


@patch('core.gmail_client.is_authorized', return_value=True)
def test_dispatch_remote_email_failure_does_not_raise(mock_is_auth, monkeypatch):
    monkeypatch.setattr(alerts.settings, 'get', lambda key: {
        'alerts:email_to': 'someone@example.com',
        'alerts:email_min_level': 'error',
    }.get(key, ''))
    with patch('core.gmail_client.send_email', side_effect=Exception("boom")):
        # Must not raise -- alert dispatch always continues.
        alerts.send_alert('Crash', 'Something broke', level='critical')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_alerts.py -v`
Expected: FAIL — `_severity_rank` doesn't exist yet (`AttributeError`), and the email-dispatch tests fail because no email branch exists yet to call `gmail_client.send_email`.

- [ ] **Step 3: Implement in `core/alerts.py`**

Add the severity-rank helper near the top of the file (after the existing module docstring/imports, before `send_alert`):

```python
_SEVERITY_RANK = {'info': 1, 'success': 2, 'warning': 3, 'error': 4, 'critical': 5}


def _severity_rank(level: str, default: str = 'info') -> int:
    return _SEVERITY_RANK.get((level or '').lower(), _SEVERITY_RANK[default])
```

Replace the existing `_get_ntfy_priority` function body to use it (same public behavior, same return values, now backed by one shared severity table instead of a second hardcoded dict):

```python
def _get_ntfy_priority(level: str) -> str:
    return str(_severity_rank(level, default='warning'))
```

Add the email branch to `_dispatch_remote`, after the existing NTFY block:

```python
def _dispatch_remote(alert: dict):
    """Optionally send to ntfy.sh, custom webhook, or email."""
    # NTFY Integration
    ntfy_url = settings.get('alerts:ntfy_url')
    if ntfy_url:
        try:
            # Fire and forget (mostly)
            headers = {"Title": alert['title'], "Priority": _get_ntfy_priority(alert['level'])}
            httpx.post(ntfy_url, content=alert['message'], headers=headers, timeout=2.0)
        except Exception as e:
            logger.debug(f"NTFY failed: {e}", ext="alerts")

    # Email Integration
    email_to = settings.get('alerts:email_to')
    min_level = settings.get('alerts:email_min_level') or 'error'
    if email_to and _severity_rank(alert['level']) >= _severity_rank(min_level, default='error'):
        try:
            from core import gmail_client
            if gmail_client.is_authorized():
                ok, err = gmail_client.send_email(subject=alert['title'], body=alert['message'])
                if not ok:
                    logger.debug(f"Email alert failed: {err}", ext="alerts")
        except Exception as e:
            logger.debug(f"Email alert failed: {e}", ext="alerts")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alerts.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the full test_gmail_client.py and test_settings_schema_gmail.py files too**

Run: `pytest tests/test_gmail_client.py tests/test_settings_schema_gmail.py tests/test_alerts.py -v`
Expected: all PASS (20 tests total).

- [ ] **Step 6: Commit**

```bash
git add core/alerts.py tests/test_alerts.py
git commit -m "feat: add email alert channel to alert bus dispatch"
```

---

### Task 4: `api/routes/utils.py` — Gmail status/authorize endpoints

**Files:**
- Modify: `api/routes/utils.py`
- Test: `tests/test_gmail_routes.py` (new)

**Interfaces:**
- Consumes: `gmail_client.is_authorized`, `gmail_client.ensure_authorized` (Task 1).
- Produces: `GET /api/utils/gmail_status` → `{'authorized': bool}`; `POST /api/utils/gmail_authorize` → `{'ok': bool}` or `{'ok': False, 'detail': str}`. Consumed by Task 5's UI panel.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gmail_routes.py`:

```python
from unittest.mock import patch
from fastapi.testclient import TestClient

from api.main import app


def test_gmail_status_returns_authorized_true():
    with patch('core.gmail_client.is_authorized', return_value=True):
        client = TestClient(app)
        resp = client.get('/api/utils/gmail_status')
    assert resp.status_code == 200
    assert resp.json() == {'authorized': True}


def test_gmail_status_returns_authorized_false():
    with patch('core.gmail_client.is_authorized', return_value=False):
        client = TestClient(app)
        resp = client.get('/api/utils/gmail_status')
    assert resp.status_code == 200
    assert resp.json() == {'authorized': False}


def test_gmail_authorize_success():
    with patch('core.gmail_client.ensure_authorized', return_value=(True, '')):
        client = TestClient(app)
        resp = client.post('/api/utils/gmail_authorize')
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}


def test_gmail_authorize_failure_returns_detail():
    with patch('core.gmail_client.ensure_authorized', return_value=(False, 'Credentials file not found')):
        client = TestClient(app)
        resp = client.post('/api/utils/gmail_authorize')
    assert resp.status_code == 200
    assert resp.json() == {'ok': False, 'detail': 'Credentials file not found'}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gmail_routes.py -v`
Expected: FAIL with 404 (routes don't exist yet).

- [ ] **Step 3: Implement in `api/routes/utils.py`**

Add immediately after the existing `gdrive_authorize` endpoint:

```python
@router.get("/utils/gmail_status")
def gmail_status():
    """Check whether the Gmail send-alerts OAuth token exists."""
    from core.gmail_client import is_authorized
    return {'authorized': is_authorized()}


@router.post("/utils/gmail_authorize")
def gmail_authorize():
    """
    Run the Gmail OAuth flow (opens a browser window), authorising the
    gmail.send scope used by the alert-email channel.
    Returns {ok: true} on success or {ok: false, detail: '...'} on failure.
    """
    from core.gmail_client import ensure_authorized
    ok, err = ensure_authorized()
    if ok:
        return {'ok': True}
    return {'ok': False, 'detail': err}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gmail_routes.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add api/routes/utils.py tests/test_gmail_routes.py
git commit -m "feat: add /utils/gmail_status and /utils/gmail_authorize endpoints"
```

---

### Task 5: `frontend/utils.html` — Gmail Alerts panel

**Files:**
- Modify: `frontend/utils.html`
- Modify: `frontend/static/i18n/en.json`
- Modify: `frontend/static/i18n/es.json`
- Modify: `frontend/static/i18n/fr.json`

**Interfaces:**
- Consumes: `GET /api/utils/gmail_status`, `POST /api/utils/gmail_authorize` (Task 4).

No automated test for this task — frontend HTML/JS in this project isn't unit tested anywhere (the Drive Bridge panel it mirrors has none either); verified manually in Task 6.

- [ ] **Step 1: Add the accordion panel to `frontend/utils.html`**

Find the existing Google Drive accordion block:

```html
        <div class="lc-bar-badge" onclick="lcAccordion(this)">
          <div class="lc-bar-tab" style="background:#334400;">&#9729;</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name" data-i18n="utils.gdrive_title">Google Drive</div>
            <div class="lc-bar-desc" id="gdrive-status-desc">Loading...</div>
            <div class="lc-bar-arrow">&#9654;</div>
          </div>
        </div>
        <div class="lc-bar-expand" id="gdrive-expand">
          <div style="display:flex;gap:8px;align-items:center;">
            <button class="lc-btn lc-btn-sm" id="gdrive-auth-btn" onclick="authoriseGDrive()" data-i18n="utils.gdrive_auth">Authorise</button>
            <span id="gdrive-auth-status" style="font-size:11px;color:var(--c-label);"></span>
          </div>
          <div id="gdrive-content" style="margin-top:10px;font-size:11px;color:var(--c-label);"></div>
        </div>
```

Add a new, near-identical block immediately after it:

```html
        <div class="lc-bar-badge" onclick="lcAccordion(this)">
          <div class="lc-bar-tab" style="background:#334400;">&#9993;</div>
          <div class="lc-bar-body">
            <div class="lc-bar-name" data-i18n="utils.gmail_title">Gmail Alerts</div>
            <div class="lc-bar-desc" id="gmail-status-desc">Loading...</div>
            <div class="lc-bar-arrow">&#9654;</div>
          </div>
        </div>
        <div class="lc-bar-expand" id="gmail-expand">
          <div style="display:flex;gap:8px;align-items:center;">
            <button class="lc-btn lc-btn-sm" id="gmail-auth-btn" onclick="authoriseGmail()" data-i18n="utils.gmail_auth">Authorise</button>
            <span id="gmail-auth-status" style="font-size:11px;color:var(--c-label);"></span>
          </div>
        </div>
```

- [ ] **Step 2: Add the JS functions**

Find the existing `loadGDriveStatus`/`authoriseGDrive` functions:

```javascript
    async function loadGDriveStatus() {
      try {
        const data = await api('/utils/gdrive_status');
        const desc = document.getElementById('gdrive-status-desc');
        if (data.authorized) {
          desc.textContent = t('utils.gdrive_authorized');
          desc.style.color = 'var(--c-ok)';
        } else {
          desc.textContent = t('utils.gdrive_unauthorized');
          desc.style.color = 'var(--c-label)';
        }
      } catch (_) {
        document.getElementById('gdrive-status-desc').textContent = t('utils.gdrive_status_unavailable');
      }
    }

    async function authoriseGDrive() {
      const btn = document.getElementById('gdrive-auth-btn');
      const status = document.getElementById('gdrive-auth-status');
      btn.disabled = true; btn.textContent = t('utils.gdrive_opening');
      try {
        const data = await api('/utils/gdrive_authorize', { method: 'POST' });
        if (data.ok) {
          status.textContent = t('utils.gdrive_success');
          status.style.color = 'var(--c-ok)';
        } else {
          status.textContent = t('utils.gdrive_error', { error: data.detail });
          status.style.color = 'var(--c-error)';
        }
      } catch (err) {
        status.textContent = t('utils.gdrive_error', { error: err.message });
        status.style.color = 'var(--c-error)';
      }
      btn.disabled = false; btn.textContent = t('utils.gdrive_auth');
      loadGDriveStatus();
    }
```

Add near-identical functions for Gmail immediately after:

```javascript
    async function loadGmailStatus() {
      try {
        const data = await api('/utils/gmail_status');
        const desc = document.getElementById('gmail-status-desc');
        if (data.authorized) {
          desc.textContent = t('utils.gmail_authorized');
          desc.style.color = 'var(--c-ok)';
        } else {
          desc.textContent = t('utils.gmail_unauthorized');
          desc.style.color = 'var(--c-label)';
        }
      } catch (_) {
        document.getElementById('gmail-status-desc').textContent = t('utils.gmail_status_unavailable');
      }
    }

    async function authoriseGmail() {
      const btn = document.getElementById('gmail-auth-btn');
      const status = document.getElementById('gmail-auth-status');
      btn.disabled = true; btn.textContent = t('utils.gmail_opening');
      try {
        const data = await api('/utils/gmail_authorize', { method: 'POST' });
        if (data.ok) {
          status.textContent = t('utils.gmail_success');
          status.style.color = 'var(--c-ok)';
        } else {
          status.textContent = t('utils.gmail_error', { error: data.detail });
          status.style.color = 'var(--c-error)';
        }
      } catch (err) {
        status.textContent = t('utils.gmail_error', { error: err.message });
        status.style.color = 'var(--c-error)';
      }
      btn.disabled = false; btn.textContent = t('utils.gmail_auth');
      loadGmailStatus();
    }
```

- [ ] **Step 3: Wire `loadGmailStatus()` into the ready listener**

Find:

```javascript
    document.addEventListener('lc:ready', () => {
      loadSearchMode();
      loadGDriveStatus();
      loadArtVaultFilter();
      loadArtIssues(0);
    });
```

Replace with:

```javascript
    document.addEventListener('lc:ready', () => {
      loadSearchMode();
      loadGDriveStatus();
      loadGmailStatus();
      loadArtVaultFilter();
      loadArtIssues(0);
    });
```

- [ ] **Step 4: Add i18n keys to all three language files**

Find the `utils.gdrive_*` block in `frontend/static/i18n/en.json`:

```json
  "utils.gdrive_title": "Google Drive",
  "utils.gdrive_auth": "Authorise",
  "utils.gdrive_authorized": "Authorised — ready for Google Drive extraction",
  "utils.gdrive_unauthorized": "Not authorised — click to set up",
```

Add immediately after it (same file):

```json
  "utils.gmail_title": "Gmail Alerts",
  "utils.gmail_auth": "Authorise",
  "utils.gmail_authorized": "Authorised — alert emails enabled",
  "utils.gmail_unauthorized": "Not authorised — click to set up",
```

Find the `utils.gdrive_status_unavailable`/`_opening`/`_success`/`_error` block in the same file:

```json
  "utils.gdrive_status_unavailable": "Status unavailable",
  "utils.gdrive_opening":           "Opening…",
  "utils.gdrive_success":           "Success",
  "utils.gdrive_error":             "Error: {error}",
```

Add immediately after it:

```json
  "utils.gmail_status_unavailable": "Status unavailable",
  "utils.gmail_opening":            "Opening…",
  "utils.gmail_success":            "Success",
  "utils.gmail_error":              "Error: {error}",
```

Repeat the same four-plus-four key additions in `frontend/static/i18n/es.json`, using the existing Spanish `utils.gdrive_*` translations as the phrasing model:

```json
  "utils.gmail_title": "Gmail Alertas",
  "utils.gmail_auth": "Autorizar",
  "utils.gmail_authorized": "Autorizado — alertas por correo habilitadas",
  "utils.gmail_unauthorized": "No autorizado — clic para configurar",
```
```json
  "utils.gmail_status_unavailable": "Estado no disponible",
  "utils.gmail_opening":            "Abriendo…",
  "utils.gmail_success":            "Éxito",
  "utils.gmail_error":              "Error: {error}",
```

And in `frontend/static/i18n/fr.json`:

```json
  "utils.gmail_title": "Alertes Gmail",
  "utils.gmail_auth": "Autoriser",
  "utils.gmail_authorized": "Autorisé — alertes par e-mail activées",
  "utils.gmail_unauthorized": "Non autorisé — cliquer pour configurer",
```
```json
  "utils.gmail_status_unavailable": "Statut non disponible",
  "utils.gmail_opening":            "Ouverture…",
  "utils.gmail_success":            "Succès",
  "utils.gmail_error":              "Erreur : {error}",
```

- [ ] **Step 5: Run the i18n parity test to confirm no new gap was introduced**

Run: `pytest tests/test_i18n.py -v`
Expected: same result as before this task (still fails only on the 3 pre-existing unrelated `vault.missing.*` keys — must NOT show any new missing `utils.gmail_*` key in the failure output).

- [ ] **Step 6: Commit**

```bash
git add frontend/utils.html frontend/static/i18n/en.json frontend/static/i18n/es.json frontend/static/i18n/fr.json
git commit -m "feat: add Gmail Alerts panel to Utils page"
```

---

### Task 6: End-to-end wiring check and manual verification

**Files:** none (verification only).

No code changes in this task — Tasks 1-5 are already fully wired (Task 3 imports `core.gmail_client` directly; Task 4 imports it too; Task 5 calls Task 4's endpoints). This task is the manual, real-world confirmation that the whole chain works end to end, since none of the automated tests exercise a real Gmail OAuth consent or a real sent email.

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -q`
Expected: only the same pre-existing unrelated failures remain (as of this plan: `test_embed_throughput_settings.py`, `test_embedding_worker.py` ×3, `test_i18n.py`'s 3 pre-existing missing keys, `test_search_throttle.py`) — no new failures, no new collection errors.

- [ ] **Step 2: Start the DocVault server**

Run `start.ps1` (or however the server is normally started in this environment) and confirm it comes up with no errors related to `core.gmail_client` or the new settings/routes.

- [ ] **Step 3: Configure the alert email recipient**

Open the Settings page, find `alerts:email_to` (in the `general` group, next to `alerts:ntfy_url`), and set it to `REDACTED-personal-email`. Leave `alerts:email_min_level` at its default (`error`) unless a different threshold is wanted.

- [ ] **Step 4: Authorize Gmail**

Open the Utils page, find the new "Gmail Alerts" panel, click "Authorise." A browser window should open for Google's OAuth consent screen (this uses the same `google:credentials_path` client secrets file already configured for Google Drive — if that isn't set up yet, `ensure_authorized()` will report "Credentials file not found," matching the Drive Bridge's own behavior in that case). Grant consent for the `gmail.send` scope. Confirm the panel then shows "Authorised — alert emails enabled."

- [ ] **Step 5: Trigger a real alert and confirm delivery**

From a Python shell in the venv (or a temporary script), run:
```python
from core.alerts import send_alert
send_alert('Test Alert', 'This is a manual end-to-end test of the Gmail alert channel.', level='critical')
```
Confirm an email arrives at `REDACTED-personal-email` with subject "Test Alert" and the given body.

- [ ] **Step 6: Confirm the severity threshold actually filters**

Run:
```python
from core.alerts import send_alert
send_alert('Should Not Email', 'This is below the configured threshold.', level='info')
```
Confirm NO email arrives for this one (with `alerts:email_min_level` still at its default `error`), while the console/log line and in-session toast still appear as normal.

- [ ] **Step 7: Report results**

No commit needed for this task (verification only) — report the outcome of steps 5 and 6 back before considering the feature complete.
