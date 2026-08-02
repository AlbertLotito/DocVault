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
