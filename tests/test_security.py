"""
Security hardening tests:
  A — server:host / server:port in settings schema
  B — open_path vault jailing + blocked extension enforcement
"""
import os
import pytest
from unittest.mock import patch


# ── A: server settings ────────────────────────────────────────────────────────

def test_server_host_in_schema():
    from core.settings import settings
    assert 'server:host' in settings.schema
    entry = settings.schema['server:host']
    assert entry['type'] == 'string'
    assert entry['default'] == '127.0.0.1'
    assert entry['group'] == 'server'


def test_server_port_in_schema():
    from core.settings import settings
    assert 'server:port' in settings.schema
    entry = settings.schema['server:port']
    assert entry['type'] == 'int'
    assert entry['default'] == 8000
    assert entry['group'] == 'server'


def test_server_host_default_is_loopback():
    from core.settings import settings
    # If no DB override, schema default must be loopback (not 0.0.0.0)
    host = settings.get('server:host') or settings.schema['server:host']['default']
    assert host == '127.0.0.1'


# ── B: open_path — blocked extensions ─────────────────────────────────────────

def test_blocked_extension_exe(tmp_path):
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    resp = client.post('/api/utils/open_path', json={'path': str(tmp_path / 'malware.exe'), 'action': 'file'})
    assert resp.status_code == 200
    assert resp.json()['status'] == 'error'
    assert 'not allowed' in resp.json()['detail'].lower()


@pytest.mark.parametrize('ext', ['.bat', '.ps1', '.vbs', '.cmd', '.scr', '.dll', '.lnk'])
def test_blocked_extensions(ext, tmp_path):
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    resp = client.post('/api/utils/open_path', json={'path': str(tmp_path / f'evil{ext}'), 'action': 'file'})
    assert resp.status_code == 200
    assert resp.json()['status'] == 'error'
    assert 'not allowed' in resp.json()['detail'].lower()


# ── B: open_path — vault jailing ──────────────────────────────────────────────

def test_open_path_outside_vault_rejected():
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    # C:\Windows is never a vault root
    resp = client.post('/api/utils/open_path', json={'path': r'C:\Windows\System32\calc.exe', 'action': 'file'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['status'] == 'error'
    # Could be blocked extension OR outside vault — either is correct
    assert data['detail'] in ('File type not allowed', 'Path is outside vault boundaries')


def test_open_path_inside_vault_allowed(tmp_path, monkeypatch):
    """A .pdf inside a mocked vault root should pass the vault check (path-not-found stops it,
    not the security checks)."""
    from api.routes.utils import _is_in_vault_root

    fake_vault_root = str(tmp_path)
    fake_vaults = [{'state': 'active', 'scan_directory': fake_vault_root}]

    with patch('api.routes.utils.VaultManager') as MockVM:
        MockVM.return_value.list_vaults.return_value = fake_vaults
        result = _is_in_vault_root(str(tmp_path / 'document.pdf'))
    assert result is True


def test_open_path_symlink_escape_blocked(tmp_path, monkeypatch):
    """A symlink that resolves outside the vault root must be rejected."""
    from api.routes.utils import _is_in_vault_root

    vault_dir = tmp_path / 'vault'
    vault_dir.mkdir()
    outside_dir = tmp_path / 'outside'
    outside_dir.mkdir()
    outside_file = outside_dir / 'secret.txt'
    outside_file.write_text('secret')

    # Create a symlink inside the vault pointing outside
    link = vault_dir / 'escape.txt'
    try:
        link.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks not supported on this platform/config')

    fake_vaults = [{'state': 'active', 'scan_directory': str(vault_dir)}]
    with patch('api.routes.utils.VaultManager') as MockVM:
        MockVM.return_value.list_vaults.return_value = fake_vaults
        result = _is_in_vault_root(str(link))
    assert result is False


def test_is_in_vault_root_gutted_vault_excluded(tmp_path):
    """Gutted/deleted vaults must not count as valid roots."""
    from api.routes.utils import _is_in_vault_root

    fake_vaults = [{'state': 'gutted', 'scan_directory': str(tmp_path)}]
    with patch('api.routes.utils.VaultManager') as MockVM:
        MockVM.return_value.list_vaults.return_value = fake_vaults
        result = _is_in_vault_root(str(tmp_path / 'doc.pdf'))
    assert result is False
