# Gmail Alert Channel — Design Spec
**Date:** 2026-08-02
**Status:** Approved

---

## 1. Problem

`core/alerts.py`'s alert bus (`send_alert()`) currently reaches the user through two channels: the in-session UI toast queue, and an optional ntfy.sh push notification. Both require the user to be actively looking at the app (toasts) or already have ntfy.sh set up on a device (push). There's no way to be notified of a serious problem (a crash, an emergency stop, a pipeline failure) somewhere the user is guaranteed to eventually check — email.

## 2. Goals

- Add email as a third alert channel, delivered via the Gmail API.
- A configurable minimum severity threshold (`alerts:email_min_level`, default `error`) controls which alerts get emailed — critical and error by default, not every routine info/success/warning event.
- A configurable recipient address (`alerts:email_to`).
- One-time OAuth2 authorization via a "Gmail Alerts" panel in the Utils page, mirroring the existing Google Drive Bridge's authorize flow exactly.
- Reuse the existing Google OAuth client (`google:credentials_path`) already configured for the Drive Bridge extractor — no new Google Cloud Console setup required.

## 3. Non-Goals

- No sending email for anything other than the alert bus (no "email this document," no scheduled digests/reports — those are separate possible future features, not in scope here).
- No new Google Cloud OAuth client/project — reuses the existing `google:credentials_path` client secrets file.
- No multi-recipient support (`alerts:email_to` is a single address for v1 — YAGNI; can extend later if needed).
- No retry/queueing on send failure — matches the existing ntfy channel's "fire and forget, log on failure" behavior exactly; alerts are still visible via the console log and in-session toast queue regardless of whether the email send succeeds.
- No widening the Drive Bridge's existing OAuth token/scope — a separate, narrowly-scoped token is used instead (see Architecture).

## 4. Architecture

A new module, `core/gmail_client.py`, holds all Gmail-specific logic, following `extractors/gdrive_extractor.py`'s existing OAuth pattern exactly (same `InstalledAppFlow` one-time browser consent, same token-refresh-on-expiry logic, same `googleapiclient.discovery.build()` call shape) but with its own scope and token file:

- **Scope**: `https://www.googleapis.com/auth/gmail.send` — send-only, cannot read the mailbox. Narrower than Drive Bridge's `drive.readonly`, and deliberately kept in its own token file rather than added to Drive Bridge's existing token, so the two features have independent, minimally-scoped credentials and authorizing one never requires re-authorizing the other.
- **Settings**: reuses `google:credentials_path` (existing) for the OAuth client secrets file; adds `gmail:token_path` (new) for the send-scoped token — same two-setting shape as Drive Bridge's `google:credentials_path`/`google:token_path` pair, just a different token file so the two scopes never collide in one token.
- **No new dependency**: `google-auth-oauthlib` and `google-api-python-client` are already installed (used by `gdrive_extractor.py`); `requirements.txt` doesn't list them today since they're an optional kernel-level dependency declared in that extractor's own `MANIFEST["requires"]`, not a core dependency — this feature adds no new entry there either, since it uses the same already-installed libraries.

### `core/gmail_client.py` — public functions

- `is_authorized() -> bool` — same shape as `gdrive_extractor.is_authorized()`: true if the token file exists.
- `ensure_authorized() -> tuple[bool, str]` — same shape as `gdrive_extractor.ensure_authorized()`: runs the one-time browser OAuth consent flow if no token exists, returns `(True, '')` on success or `(False, error_message)` on failure (missing credentials file, flow error, etc.).
- `send_email(subject: str, body: str) -> tuple[bool, str]` — builds a plain-text RFC822 message via stdlib `email.mime.text.MIMEText`, base64url-encodes it (`base64.urlsafe_b64encode`), and calls the Gmail API's `users.messages.send` (no explicit `From` — Gmail API defaults it to the authenticated account). Returns `(True, '')` on success, `(False, error_message)` on any failure (not authorized, API error, network error) — never raises, matching `_dispatch_remote`'s existing "fire and forget" contract.

### `core/alerts.py` changes

- New helper `_severity_rank(level: str) -> int`, reusing the same critical=5/error=4/warning=3/success=2/info=1 ordering `_get_ntfy_priority` already encodes (extracted so both ntfy and the new email channel share one source of truth for severity ordering, rather than duplicating the mapping).
- `_dispatch_remote(alert)` gains an email branch: reads `alerts:email_min_level` (default `'error'`) and `alerts:email_to`; if both are set, `_severity_rank(alert['level']) >= _severity_rank(email_min_level)`, and `gmail_client.is_authorized()`, calls `gmail_client.send_email(subject=alert['title'], body=alert['message'])`. Any failure is logged via `logger.debug` and swallowed, exactly like the existing ntfy branch — an email failure must never block or crash alert dispatch.
- Import of `core.gmail_client` is deferred (inside the function, like `gdrive_extractor` is imported lazily elsewhere) so `core/alerts.py`'s module-level import stays free of the Gmail API library dependency for installs that never touch this feature.

### New settings (schema-driven, auto-appear in Settings UI exactly like `alerts:ntfy_url` does)

| Key | Type | Default | Description |
|---|---|---|---|
| `alerts:email_min_level` | string | `error` | Minimum alert severity (`info`/`success`/`warning`/`error`/`critical`) that triggers an email. An unrecognized value falls back to `error`'s rank rather than raising. |
| `alerts:email_to` | string | `` (empty) | Recipient email address. Empty disables the channel entirely (same "empty disables" convention as `alerts:ntfy_url`). |
| `gmail:token_path` | string | `` (empty) | Path to the OAuth token file for the `gmail.send` scope. Empty means not yet authorized. |

### New endpoints (`api/routes/utils.py`), mirroring the Drive Bridge pair exactly

- `GET /utils/gmail_status` → `{'authorized': gmail_client.is_authorized()}`.
- `POST /utils/gmail_authorize` → runs `gmail_client.ensure_authorized()`, returns `{'ok': ok, 'detail': err}`.

### UI (`frontend/utils.html`)

A new "Gmail Alerts" accordion panel, placed alongside the existing "Google Drive" panel, copying its exact structure: a status badge (`loadGmailStatus()` calling `/utils/gmail_status`, mirroring `loadGDriveStatus()`), an "Authorise" button (`authoriseGmail()` calling `POST /utils/gmail_authorize`, mirroring `authoriseGDrive()`), and the same success/error status-text styling. The `alerts:email_min_level`/`alerts:email_to` settings themselves are NOT duplicated here — they already appear in the Settings page automatically via the schema, next to the existing `alerts:ntfy_url` field, matching how every other setting is exposed.

## 5. Error Handling

| Condition | Behavior |
|---|---|
| `alerts:email_to` empty | Email channel silently skipped (same as ntfy's empty-`ntfy_url` behavior). |
| Not yet authorized (`gmail_client.is_authorized()` false) | Email channel silently skipped — alert still logged to console + in-session toast queue as normal. |
| Alert severity below `alerts:email_min_level` | Email channel skipped for that alert; other channels unaffected. |
| Gmail API send fails (network error, token revoked, quota) | Caught, logged via `logger.debug`, alert dispatch continues normally — never raises, never blocks the caller of `send_alert()`. |
| `ensure_authorized()` fails (missing credentials file, user closes browser without granting consent) | Returns `(False, error_message)`; the Utils UI shows the error message inline, same as the Drive Bridge's existing authorize-failure display. |

## 6. Testing

- `core/gmail_client.py`: unit tests mocking `googleapiclient.discovery.build` and the OAuth flow objects — `is_authorized()` against a present/absent token file, `send_email()` success and failure paths, matching the mocking style already used for other Google-API-adjacent code in this codebase.
- `core/alerts.py`: unit tests for `_severity_rank()` (ordering correctness, unrecognized-level fallback) and `_dispatch_remote()`'s new email branch — email sent when severity clears the bar and the channel is configured/authorized, skipped when any of those isn't true, using mocks for `gmail_client.send_email`/`is_authorized` (no real network calls, no real Gmail API access in the test suite).
- Manual verification (OAuth consent and an actual sent email can't be meaningfully automated): authorize via the new Utils panel, trigger a `send_alert(level='error', ...)` somewhere in the app, confirm the email arrives at the configured address; confirm an `info`-level alert with the default `error` threshold does NOT send an email.

## 7. Rollout

Purely additive: one new module (`core/gmail_client.py`), two new endpoints, one new UI panel, three new settings, and a small addition to `core/alerts.py`'s existing dispatch function. No new dependency (reuses libraries already installed for the Drive Bridge extractor). No migration needed — the feature is inert (silently skipped) until a user both authorizes it and sets `alerts:email_to`.
