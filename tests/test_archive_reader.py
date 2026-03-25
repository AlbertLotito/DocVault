"""
Unit tests for core/archive_reader.py
"""
import io
import os
import sys
import gzip
import zipfile
import tarfile
import tempfile
import unittest.mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core import archive_reader


# ── helpers ──────────────────────────────────────────────────────────────────

def make_zip(files: dict) -> str:
    """files: {name: content_str}. Returns temp file path."""
    f = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    f.close()
    with zipfile.ZipFile(f.name, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return f.name


def make_tar(files: dict, suffix='.tar') -> str:
    """files: {name: content_str}. Returns temp file path."""
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    mode = 'w:gz' if suffix in ('.tar.gz', '.tgz') else 'w:'
    with tarfile.open(f.name, mode) as tf:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return f.name


def make_gz(content: bytes) -> str:
    """Standalone .gz (not a tarball)."""
    f = tempfile.NamedTemporaryFile(suffix='.gz', delete=False)
    f.close()
    with gzip.open(f.name, 'wb') as gf:
        gf.write(content)
    return f.name


# ── _classify ────────────────────────────────────────────────────────────────

def test_classify_source_code():
    assert archive_reader._classify('src/main.py') == 'Source Code'

def test_classify_document():
    assert archive_reader._classify('report.pdf') == 'Documents'

def test_classify_image():
    assert archive_reader._classify('photo.jpg') == 'Images'

def test_classify_unknown():
    assert archive_reader._classify('data.xyz') == 'Other'

def test_classify_no_extension():
    assert archive_reader._classify('Makefile') == 'Other'


# ── read_entries: ZIP ────────────────────────────────────────────────────────

def test_read_entries_zip_returns_entries():
    path = make_zip({'README.md': 'hello', 'src/app.py': 'code'})
    try:
        entries, compressed, fmt = archive_reader.read_entries(path)
        names = [e['name'] for e in entries]
        assert 'README.md' in names
        assert 'src/app.py' in names
        assert fmt == 'ZIP'
        assert isinstance(compressed, int)
    finally:
        os.unlink(path)

def test_read_entries_zip_entry_fields():
    path = make_zip({'hello.txt': 'world'})
    try:
        entries, _, _ = archive_reader.read_entries(path)
        files = [e for e in entries if not e['is_dir']]
        assert len(files) == 1
        e = files[0]
        assert e['name'] == 'hello.txt'
        assert e['size'] == len('world')
        assert e['encrypted'] is False
        assert e['is_dir'] is False
        assert isinstance(e['mtime'], str)
    finally:
        os.unlink(path)

def test_read_entries_zip_encrypted_flag():
    """ZIP entry with flag_bits & 0x1 is reported as encrypted=True."""
    path = make_zip({'plaintext.txt': 'hello'})
    try:
        # Build two ZipInfo objects: one plain, one with encryption flag set
        plain_info = zipfile.ZipInfo('plaintext.txt')
        plain_info.file_size = 5
        plain_info.compress_size = 5
        plain_info.flag_bits = 0

        enc_info = zipfile.ZipInfo('secret.bin')
        enc_info.file_size = 16
        enc_info.compress_size = 16
        enc_info.flag_bits = 0x1  # encryption flag

        with unittest.mock.patch.object(zipfile.ZipFile, 'infolist', return_value=[plain_info, enc_info]):
            entries, _, _ = archive_reader.read_entries(path)

        enc_entries = [e for e in entries if e['name'] == 'secret.bin']
        plain_entries = [e for e in entries if e['name'] == 'plaintext.txt']
        assert len(enc_entries) == 1
        assert enc_entries[0]['encrypted'] is True
        assert len(plain_entries) == 1
        assert plain_entries[0]['encrypted'] is False
    finally:
        os.unlink(path)

def test_read_entries_zip_does_not_raise_permission_error_for_encrypted():
    """read_entries on a ZIP never raises PermissionError (no global password check)."""
    path = make_zip({'a.txt': 'a'})
    try:
        # Should complete without raising PermissionError
        entries, _, fmt = archive_reader.read_entries(path)
        assert fmt == 'ZIP'
    finally:
        os.unlink(path)


# ── read_entries: TAR ────────────────────────────────────────────────────────

def test_read_entries_tar_basic():
    path = make_tar({'README.md': 'readme', 'src/app.py': 'code'})
    try:
        entries, compressed, fmt = archive_reader.read_entries(path)
        names = [e['name'] for e in entries if not e['is_dir']]
        assert 'README.md' in names
        assert fmt == 'TAR'
        assert isinstance(compressed, int)
        assert compressed > 0
    finally:
        os.unlink(path)

def test_read_entries_tar_gz_format_string():
    path = make_tar({'hello.py': 'print(1)'}, suffix='.tar.gz')
    try:
        _, _, fmt = archive_reader.read_entries(path)
        assert fmt == 'TAR.GZ'
    finally:
        os.unlink(path)

def test_read_entries_tar_entry_fields():
    path = make_tar({'data.csv': 'a,b,c'})
    try:
        entries, _, _ = archive_reader.read_entries(path)
        files = [e for e in entries if not e['is_dir']]
        assert len(files) == 1
        e = files[0]
        assert e['name'] == 'data.csv'
        assert e['size'] == len('a,b,c')
        assert e['encrypted'] is False
        assert e['is_dir'] is False
    finally:
        os.unlink(path)


# ── read_entries: standalone .gz ─────────────────────────────────────────────

def test_read_entries_standalone_gz_raises_tar_error():
    path = make_gz(b'just compressed bytes')
    try:
        with pytest.raises(tarfile.TarError):
            archive_reader.read_entries(path)
    finally:
        os.unlink(path)


# ── read_entries: missing file ───────────────────────────────────────────────

def test_read_entries_missing_file_raises_os_error():
    with pytest.raises((OSError, FileNotFoundError)):
        archive_reader.read_entries('/nonexistent/archive.zip')


# ── read_entries: missing py7zr ──────────────────────────────────────────────

def test_read_entries_7z_raises_import_error_when_py7zr_missing(tmp_path):
    fake_7z = tmp_path / "test.7z"
    fake_7z.write_bytes(b'\x37\x7a\xbc\xaf\x27\x1c')  # 7z magic bytes
    with unittest.mock.patch.object(archive_reader, '_HAS_7Z', False):
        with pytest.raises(ImportError, match='py7zr'):
            archive_reader.read_entries(str(fake_7z))
