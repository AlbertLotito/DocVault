"""
Tests for GET /catalog/archive endpoint.
"""
import io
import json
import os
import sys
import zipfile
import tarfile
import tempfile

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def client(tmp_path):
    """TestClient with a minimal in-memory DB."""
    import sqlite3
    from api.main import app
    db_path = str(tmp_path / "test.db")

    from core import manager
    manager.init_db(db_path)
    manager.insert_task(db_path, 'aaaa', str(tmp_path / 'test.zip'), 'zip')
    manager.insert_task(db_path, 'bbbb', str(tmp_path / 'test.tar'), 'tar')
    manager.insert_task(db_path, 'cccc', str(tmp_path / 'notes.pdf'), 'pdf')

    with patch('api.main.DB_PATH', db_path):
        yield TestClient(app), tmp_path


def _make_zip(path, files):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _make_tar(path, files):
    with tarfile.open(path, 'w:') as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


# ── basic success ─────────────────────────────────────────────────────────────

def test_archive_endpoint_returns_zip_listing(client):
    tc, tmp = client
    zip_path = str(tmp / 'test.zip')
    _make_zip(zip_path, {'README.md': 'hello', 'src/app.py': 'code'})
    resp = tc.get(f'/api/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    data = resp.json()
    assert data['format'] == 'ZIP'
    assert data['file_count'] == 2
    assert data['password_protected'] is False
    names = [e['name'] for e in data['entries']]
    assert 'README.md' in names
    assert 'src/app.py' in names


def test_archive_endpoint_includes_groups(client):
    tc, tmp = client
    zip_path = str(tmp / 'test.zip')
    _make_zip(zip_path, {'main.py': 'code', 'report.pdf': 'pdf data'})
    resp = tc.get(f'/api/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    group_names = [g['name'] for g in resp.json()['groups']]
    assert 'Source Code' in group_names
    assert 'Documents' in group_names


def test_archive_endpoint_returns_tar_listing(client):
    tc, tmp = client
    tar_path = str(tmp / 'test.tar')
    _make_tar(tar_path, {'hello.txt': 'world'})
    resp = tc.get(f'/api/catalog/archive?path={tar_path}')
    assert resp.status_code == 200
    data = resp.json()
    assert data['format'] == 'TAR'
    assert data['file_count'] == 1


def test_archive_endpoint_uncompressed_bytes(client):
    tc, tmp = client
    zip_path = str(tmp / 'test.zip')
    content = 'x' * 1000
    _make_zip(zip_path, {'big.txt': content})
    resp = tc.get(f'/api/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    assert resp.json()['total_uncompressed_bytes'] == 1000


# ── error cases ───────────────────────────────────────────────────────────────

def test_archive_endpoint_404_for_unknown_path(client):
    tc, tmp = client
    resp = tc.get('/api/catalog/archive?path=/no/such/file.zip')
    assert resp.status_code == 404


def test_archive_endpoint_400_for_non_archive_type(client):
    tc, tmp = client
    resp = tc.get(f'/api/catalog/archive?path={str(tmp / "notes.pdf")}')
    assert resp.status_code == 400


def test_archive_endpoint_500_for_missing_file(client):
    tc, tmp = client
    # Path is in DB but file doesn't exist on disk
    resp = tc.get(f'/api/catalog/archive?path={str(tmp / "test.zip")}')
    # File not created → read_entries raises OSError → 500
    assert resp.status_code == 500


# ── password protected ────────────────────────────────────────────────────────

def test_archive_endpoint_password_protected_returns_200(client):
    tc, tmp = client
    zip_path = str(tmp / 'test.zip')
    _make_zip(zip_path, {'a.txt': 'a'})
    with patch('core.archive_reader.read_entries', side_effect=PermissionError("Password protected")):
        resp = tc.get(f'/api/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    data = resp.json()
    assert data['password_protected'] is True
    assert data['entries'] == []
    assert data['groups'] == []
