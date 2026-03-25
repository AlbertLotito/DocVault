# Archive X-Ray Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract shared archive readers into `core/archive_reader.py`, add a `GET /catalog/archive` browse endpoint, and surface archive badge + collapsible browse panel in search results.

**Architecture:** Shared reader module `core/archive_reader.py` owns all ZIP/TAR/7Z/RAR parsing plus classification helpers; the existing extractor and the new API endpoint both import from it. The browse endpoint re-reads the archive on demand and returns structured JSON; the frontend renders it inline via a toggle panel. Auto-certification of system kernels already works via the existing `register_system_kernels()` — no code change needed there, but a regression test is added.

**Tech Stack:** Python stdlib (`zipfile`, `tarfile`), optional `py7zr` / `rarfile`, FastAPI, SQLite, vanilla JS (no frameworks).

---

## Chunk 1: Shared reader module + extractor refactor

## Task 1: Create core/archive_reader.py

**Files:**
- Create: `core/archive_reader.py`
- Create: `tests/test_archive_reader.py`

### Background

`core/archive_reader.py` will own:
- `_GROUPS`, `_GROUP_ORDER`, `_classify()` — file type classification (moved from extractor)
- `_fmt_date()` — date formatting helper used by readers
- `read_entries(file_path)` — single public function that dispatches to the right format reader
- Private readers: `_read_zip`, `_read_tar`, `_read_7z`, `_read_rar`

The existing test file `tests/test_archive_xray.py` has helpers `make_zip()` and `make_tar()` you should copy for your own tests — don't import from there, just duplicate the tiny helpers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_archive_reader.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd E:\DocVault
python -m pytest tests/test_archive_reader.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` or `ImportError` — `core/archive_reader.py` does not exist yet.

- [ ] **Step 3: Create core/archive_reader.py**

```python
"""
core/archive_reader.py — Shared archive entry reader for DocVault.

Owns all ZIP/TAR/7Z/RAR parsing logic and file-type classification.
Used by:
  - extractors/archive_xray_extractor.py  (FTS text production)
  - api/routes/catalog.py                 (browse endpoint)
"""
import datetime
import os
import tarfile
import zipfile

try:
    import py7zr as _py7zr
    _HAS_7Z = True
except ImportError:
    _HAS_7Z = False

try:
    import rarfile as _rarfile
    _HAS_RAR = True
except ImportError:
    _HAS_RAR = False


# ---------------------------------------------------------------------------
# File type classification
# ---------------------------------------------------------------------------

_GROUPS = {
    'Source Code': {
        'py', 'js', 'ts', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rb', 'php',
        'swift', 'kt', 'rs', 'sh', 'bat', 'ps1', 'lua', 'r', 'm', 'scala',
        'clj', 'ex', 'exs', 'elm', 'vue', 'jsx', 'tsx', 'coffee', 'dart',
        'nim', 'zig',
    },
    'Documents': {
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods',
        'odp', 'rtf', 'pages', 'numbers', 'key', 'epub', 'md', 'rst', 'tex',
        'txt',
    },
    'Images': {
        'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'tif', 'webp', 'svg',
        'ico', 'raw', 'cr2', 'nef', 'arw', 'dng', 'heic', 'psd', 'ai',
    },
    'Audio': {'mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac', 'opus', 'wma', 'aiff'},
    'Video': {'mp4', 'mkv', 'avi', 'mov', 'wmv', 'webm', 'flv', 'm4v', 'mpg', 'mpeg'},
    'Archives': {'zip', '7z', 'rar', 'tar', 'gz', 'bz2', 'xz', 'tgz', 'tbz2'},
    'Data': {
        'json', 'xml', 'csv', 'yaml', 'yml', 'toml', 'sql', 'db', 'sqlite',
        'sqlite3', 'parquet', 'h5', 'hdf5',
    },
}

_GROUP_ORDER = [
    'Source Code', 'Documents', 'Images', 'Audio', 'Video',
    'Archives', 'Data', 'Other',
]


def _classify(filename: str) -> str:
    """Return the group name for a filename based on its extension."""
    ext = os.path.splitext(filename)[1].lstrip('.').lower()
    for group, exts in _GROUPS.items():
        if ext in exts:
            return group
    return 'Other'


# ---------------------------------------------------------------------------
# Internal date helper
# ---------------------------------------------------------------------------

def _fmt_date(dt) -> str:
    """Accept datetime, unix timestamp, or date_time tuple (zipfile). Returns YYYY-MM-DD or ''."""
    try:
        if isinstance(dt, (int, float)):
            dt = datetime.datetime.fromtimestamp(dt)
        elif isinstance(dt, tuple) and len(dt) >= 3:
            dt = datetime.datetime(*dt[:6])
        if hasattr(dt, 'strftime'):
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_entries(file_path: str) -> tuple[list[dict], int, str]:
    """Read archive central directory without extracting content.

    Returns (entries, total_compressed_bytes, format_name).

    Each entry dict:
        name      (str)  — path inside the archive
        size      (int)  — uncompressed size in bytes (0 for directories)
        mtime     (str)  — 'YYYY-MM-DD' or '' if unavailable
        is_dir    (bool) — True for directory entries
        encrypted (bool) — True if this entry is encrypted (ZIP per-entry flag only)

    format_name values: 'ZIP', 'TAR', 'TAR.GZ', 'TAR.BZ2', 'TAR.XZ', '7Z', 'RAR'

    Password handling:
        ZIP  — no pre-read check; entries returned with encrypted=True per entry
        7Z   — raises PermissionError if archive requires a password
        RAR  — raises PermissionError if archive requires a password
        TAR  — no encryption; encrypted is always False

    Raises:
        ImportError      — py7zr (.7z) or rarfile (.rar) not installed
        PermissionError  — 7Z or RAR archive is password-protected
        tarfile.TarError — standalone .gz/.bz2 that is not a tar archive
        OSError          — file not found, unreadable, or corrupt
    """
    name_lower = os.path.basename(file_path).lower()

    if name_lower.endswith('.zip'):
        return _read_zip(file_path)
    if (name_lower.endswith('.tar')
            or name_lower.endswith('.tar.gz')
            or name_lower.endswith('.tar.bz2')
            or name_lower.endswith('.tar.xz')
            or name_lower.endswith('.tgz')
            or name_lower.endswith('.tbz2')
            or name_lower.endswith('.gz')
            or name_lower.endswith('.bz2')):
        return _read_tar(file_path)
    if name_lower.endswith('.7z'):
        return _read_7z(file_path)
    if name_lower.endswith('.rar'):
        return _read_rar(file_path)
    raise ValueError(f"Unsupported archive format: {os.path.basename(file_path)}")


# ---------------------------------------------------------------------------
# Format readers
# ---------------------------------------------------------------------------

def _read_zip(file_path: str) -> tuple:
    entries = []
    total_compressed = 0
    with zipfile.ZipFile(file_path, 'r') as zf:
        for info in zf.infolist():
            entries.append({
                'name':      info.filename,
                'size':      info.file_size,
                'mtime':     _fmt_date(info.date_time),
                'is_dir':    info.is_dir(),
                'encrypted': bool(info.flag_bits & 0x1),
            })
            total_compressed += info.compress_size
    return entries, total_compressed, 'ZIP'


def _read_tar(file_path: str) -> tuple:
    name_lower = file_path.lower()
    if name_lower.endswith(('.tar.gz', '.tgz')):
        fmt = 'TAR.GZ'
    elif name_lower.endswith(('.tar.bz2', '.tbz2')):
        fmt = 'TAR.BZ2'
    elif name_lower.endswith('.tar.xz'):
        fmt = 'TAR.XZ'
    else:
        fmt = 'TAR'

    entries = []
    with tarfile.open(file_path, 'r:*') as tf:
        for member in tf.getmembers():
            entries.append({
                'name':      member.name,
                'size':      member.size,
                'mtime':     _fmt_date(member.mtime),
                'is_dir':    member.isdir(),
                'encrypted': False,
            })
    compressed = os.path.getsize(file_path)
    return entries, compressed, fmt


def _read_7z(file_path: str) -> tuple:
    if not _HAS_7Z:
        raise ImportError("py7zr is not installed — run: pip install py7zr")
    entries = []
    total_compressed = 0
    try:
        with _py7zr.SevenZipFile(file_path, mode='r') as zf:
            if zf.needs_password():
                raise PermissionError("Password protected")
            for info in zf.list():
                entries.append({
                    'name':      info.filename,
                    'size':      info.uncompressed or 0,
                    'mtime':     _fmt_date(info.creationtime) if info.creationtime else '',
                    'is_dir':    info.is_directory,
                    'encrypted': False,
                })
                if info.compressed:
                    total_compressed += info.compressed
    except _py7zr.exceptions.PasswordRequired:
        raise PermissionError("Password protected")
    if not total_compressed:
        total_compressed = os.path.getsize(file_path)
    return entries, total_compressed, '7Z'


def _read_rar(file_path: str) -> tuple:
    if not _HAS_RAR:
        raise ImportError("rarfile is not installed — run: pip install rarfile")
    entries = []
    total_compressed = 0
    with _rarfile.RarFile(file_path) as rf:
        if rf.needs_password():
            raise PermissionError("Password protected")
        for info in rf.infolist():
            entries.append({
                'name':      info.filename,
                'size':      info.file_size,
                'mtime':     _fmt_date(info.date_time) if info.date_time else '',
                'is_dir':    info.is_dir(),
                'encrypted': False,
            })
            total_compressed += info.compress_size
    return entries, total_compressed, 'RAR'
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_archive_reader.py -v
```

Expected: all tests pass. The `test_read_entries_7z_raises_import_error_when_py7zr_missing` test patches `_HAS_7Z` to `False` so it works regardless of whether `py7zr` is installed.

- [ ] **Step 5: Commit**

```bash
git add core/archive_reader.py tests/test_archive_reader.py
git commit -m "feat(archive): add core/archive_reader.py with shared entry readers and classification"
```

---

## Task 2: Refactor archive_xray_extractor.py to use core/archive_reader

**Files:**
- Modify: `extractors/archive_xray_extractor.py`
- Verify: `tests/test_archive_xray.py` (no changes needed — all 20 tests must still pass)

### What changes

Remove from the extractor:
- `_GROUPS` dict
- `_GROUP_ORDER` list
- `_classify()` function
- `_fmt_date()` function
- `_read_zip()`, `_read_tar()`, `_read_7z()`, `_read_rar()` functions
- The `import datetime` line (no longer needed in extractor)

Add at the top (after existing imports):
```python
import tarfile
from core.archive_reader import read_entries, _classify, _GROUP_ORDER
```

(`tarfile` is needed for `except tarfile.TarError` in `extract()`.)

Replace the entire dispatch block inside `extract()` (the chain of `if name_lower.endswith('.zip')` / `elif` / `else`) with a single `read_entries()` call.

- [ ] **Step 1: Confirm current tests pass before touching anything**

```
python -m pytest tests/test_archive_xray.py -v
```

Expected: 20 passed.

- [ ] **Step 2: Apply the changes to archive_xray_extractor.py**

The new `extract()` function (find and replace the existing `def extract(file_path, ctx)` function body):

```python
def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    archive_name = os.path.basename(file_path)

    try:
        try:
            entries, total_compressed, fmt = read_entries(file_path)
        except tarfile.TarError:
            return None, "Standalone compressed file — not a tar archive", None

        meta = {
            'format': fmt,
            'file_count': len([e for e in entries if not e['is_dir']]),
            'total_size_bytes': sum(e['size'] for e in entries if not e['is_dir']),
        }
        return _build_output(archive_name, fmt, entries, total_compressed), None, meta

    except PermissionError:
        fmt_guess = os.path.splitext(archive_name)[1].lstrip('.').upper() or 'ARCHIVE'
        return (
            f"Archive: {archive_name}\n"
            f"Format: {fmt_guess}\n\n"
            f"Password protected — file listing unavailable"
        ), None, {'format': fmt_guess, 'password_protected': True}

    except ImportError as e:
        return None, str(e), None

    except Exception as e:
        return None, f"Archive read failed: {e}", None
```

Replace the existing imports block at the top of `archive_xray_extractor.py` with:

```python
import datetime
import os
import tarfile
from collections import Counter, defaultdict
from core.archive_reader import read_entries, _classify, _GROUP_ORDER
from core.extractors.base import ExtractorContext
```

Remove:
- `import zipfile` (was only used by `_read_zip`, which is removed)
- The `try/except ImportError` blocks for `py7zr` and `rarfile` (now in `core/archive_reader.py`)

Keep:
- `import datetime` — `_fmt_date` stays in the extractor per spec (it becomes dead code after the readers are removed, but is retained intentionally)
- `from collections import Counter, defaultdict` — used by `_build_output`

- [ ] **Step 3: Run the existing test suite to confirm no regressions**

```
python -m pytest tests/test_archive_xray.py -v
```

Expected: 20 passed. If any test fails, check the import paths — `_classify` is still accessible as `mod._classify` because the extractor re-imports and re-exports it at module level via `from core.archive_reader import ... _classify ...`.

- [ ] **Step 4: Run both test files together**

```
python -m pytest tests/test_archive_reader.py tests/test_archive_xray.py -v
```

Expected: all tests pass (20 + however many you wrote in Task 1).

- [ ] **Step 5: Commit**

```bash
git add extractors/archive_xray_extractor.py
git commit -m "refactor(archive): use core/archive_reader in archive_xray_extractor"
```

---

## Chunk 2: API endpoint + frontend + registry test

## Task 3: Add GET /catalog/archive endpoint

**Files:**
- Modify: `api/routes/catalog.py` (add endpoint between `/catalog/inspect` and `/{file_hash}`)
- Create: `tests/test_archive_browse_api.py`

### Background

The endpoint re-reads the archive live using `core.archive_reader.read_entries`. It validates the `file_type` column (stored as the last file extension, e.g. `backup.tar.gz` → `file_type='gz'`). Password-protected 7Z/RAR archives return 200 with `password_protected: true` and empty data.

- [ ] **Step 1: Write failing tests**

Create `tests/test_archive_browse_api.py`:

```python
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
    resp = tc.get(f'/catalog/archive?path={zip_path}')
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
    resp = tc.get(f'/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    group_names = [g['name'] for g in resp.json()['groups']]
    assert 'Source Code' in group_names
    assert 'Documents' in group_names


def test_archive_endpoint_returns_tar_listing(client):
    tc, tmp = client
    tar_path = str(tmp / 'test.tar')
    _make_tar(tar_path, {'hello.txt': 'world'})
    resp = tc.get(f'/catalog/archive?path={tar_path}')
    assert resp.status_code == 200
    data = resp.json()
    assert data['format'] == 'TAR'
    assert data['file_count'] == 1


def test_archive_endpoint_uncompressed_bytes(client):
    tc, tmp = client
    zip_path = str(tmp / 'test.zip')
    content = 'x' * 1000
    _make_zip(zip_path, {'big.txt': content})
    resp = tc.get(f'/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    assert resp.json()['total_uncompressed_bytes'] == 1000


# ── error cases ───────────────────────────────────────────────────────────────

def test_archive_endpoint_404_for_unknown_path(client):
    tc, tmp = client
    resp = tc.get('/catalog/archive?path=/no/such/file.zip')
    assert resp.status_code == 404


def test_archive_endpoint_400_for_non_archive_type(client):
    tc, tmp = client
    resp = tc.get(f'/catalog/archive?path={str(tmp / "notes.pdf")}')
    assert resp.status_code == 400


def test_archive_endpoint_500_for_missing_file(client):
    tc, tmp = client
    # Path is in DB but file doesn't exist on disk
    resp = tc.get(f'/catalog/archive?path={str(tmp / "test.zip")}')
    # File not created → read_entries raises OSError → 500
    assert resp.status_code == 500


# ── password protected ────────────────────────────────────────────────────────

def test_archive_endpoint_password_protected_returns_200(client):
    tc, tmp = client
    zip_path = str(tmp / 'test.zip')
    _make_zip(zip_path, {'a.txt': 'a'})
    with patch('core.archive_reader.read_entries', side_effect=PermissionError("Password protected")):
        resp = tc.get(f'/catalog/archive?path={zip_path}')
    assert resp.status_code == 200
    data = resp.json()
    assert data['password_protected'] is True
    assert data['entries'] == []
    assert data['groups'] == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_archive_browse_api.py -v 2>&1 | head -20
```

Expected: errors because the endpoint doesn't exist yet.

- [ ] **Step 3: Add the endpoint to api/routes/catalog.py**

Insert the new endpoint and imports. Add after the existing imports at the top:

```python
from collections import defaultdict
```

Insert the `_ARCHIVE_EXTENSIONS` constant and `browse_archive` endpoint between the `/catalog/inspect` route (line ~29) and the `/{file_hash}` route (line ~76). The final order must be:

```
GET /catalog
GET /catalog/inspect
GET /catalog/archive    ← new, inserted here
GET /catalog/{file_hash}
POST /catalog/{file_hash}/reprocess
```

Code to insert:

```python
_ARCHIVE_EXTENSIONS = frozenset({
    'zip', 'tar', 'tgz', 'tbz2', 'gz', 'bz2', '7z', 'rar'
})


@router.get("/catalog/archive")
def browse_archive(path: str = Query(...)):
    """Return structured archive listing without extracting content."""
    import tarfile as _tarfile
    from core.archive_reader import read_entries, _classify, _GROUP_ORDER

    db = get_db()

    # 1. Look up by file_path
    with sqlite3.connect(db, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT file_type FROM tasks WHERE file_path = ?", (path,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File not found in database")
        file_type = (row['file_type'] or '').lower()

    # 2. Validate archive type
    if file_type not in _ARCHIVE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Not an archive file type: {file_type!r}"
        )

    # 3. Read entries
    try:
        try:
            entries, total_compressed, fmt = read_entries(path)
        except _tarfile.TarError:
            raise HTTPException(
                status_code=400,
                detail="Standalone compressed file — not a tar archive"
            )
    except HTTPException:
        raise
    except PermissionError:
        return {
            "format": file_type.upper(),
            "file_count": 0,
            "total_uncompressed_bytes": 0,
            "total_compressed_bytes": 0,
            "password_protected": True,
            "groups": [],
            "entries": [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read archive: {e}")

    # 4. Build response
    files = [e for e in entries if not e['is_dir']]
    group_counts: dict = defaultdict(int)
    for e in files:
        group_counts[_classify(e['name'])] += 1
    groups = [
        {"name": g, "count": group_counts[g]}
        for g in _GROUP_ORDER
        if g in group_counts
    ]
    total_uncompressed = sum(e['size'] for e in files)

    return {
        "format": fmt,
        "file_count": len(files),
        "total_uncompressed_bytes": total_uncompressed,
        "total_compressed_bytes": total_compressed,
        "password_protected": False,
        "groups": groups,
        "entries": files[:500],
    }
```

- [ ] **Step 4: Run the tests**

```
python -m pytest tests/test_archive_browse_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the full test suite (excluding pre-existing broken tests)**

```
python -m pytest --ignore=tests/test_router.py --ignore=tests/test_video_extractor.py -q
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add api/routes/catalog.py tests/test_archive_browse_api.py
git commit -m "feat(api): add GET /catalog/archive browse endpoint"
```

---

## Task 4: Frontend — archive badge, browse toggle, panel

**Files:**
- Modify: `frontend/search.html`

### What changes

1. Add CSS for `.archive-badge`, `.archive-browse-panel`, `.archive-group-pills`, `.archive-group-pill`, `.archive-entry-row` in the `<style>` block.
2. Extend `formatSize()` to handle GB.
3. Add `ARCHIVE_TYPES` set and `isArchivePath()` helper.
4. Update `renderResults()` to detect archives and add badge + Browse button.
5. Update `renderFilenameResults()` to detect archives, add badge + Browse button + meta line.
6. Add `browseArchive()` async function.

No automated tests for frontend — verify manually by running the app and searching for a ZIP/TAR file.

- [ ] **Step 1: Add CSS to the `<style>` block**

Find the closing `</style>` tag (it's just before `</head>`) and insert before it:

```css
    /* ── Archive browse ──────────────────────────────────────────────────── */
    .archive-badge {
      background: #2a1400;
      color: var(--c-warn);
      border: 1px solid var(--c-warn);
      font-size: 9px;
      padding: 1px 5px;
      font-family: var(--font);
      letter-spacing: 1px;
      text-transform: uppercase;
      flex-shrink: 0;
    }
    .archive-browse-panel {
      background: #110800;
      border: 1px solid var(--c-border);
      border-top: none;
      padding: 10px 14px;
      margin-bottom: 6px;
      font-family: monospace;
      font-size: 11px;
    }
    .archive-panel-header {
      color: var(--c-label);
      margin-bottom: 8px;
    }
    .archive-group-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
    }
    .archive-group-pill {
      background: #1a0d00;
      color: var(--c-accent-dim);
      border: 1px solid var(--c-border);
      padding: 1px 7px;
      font-size: 10px;
      font-family: var(--font);
    }
    .archive-entry-row {
      display: flex;
      gap: 8px;
      padding: 1px 0;
      color: #cc9966;
      white-space: nowrap;
      overflow: hidden;
    }
    .archive-entry-name {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .archive-entry-size { color: var(--c-label); min-width: 60px; text-align: right; }
    .archive-entry-date { color: var(--c-label-dim); min-width: 80px; }
    .archive-entry-enc  { color: var(--c-warn); }
    .archive-more { color: var(--c-label-dim); margin-top: 6px; font-size: 10px; }
    .archive-error { color: var(--c-error); }
```

- [ ] **Step 2: Extend formatSize() and add archive helpers**

Find and replace the existing `formatSize` function (lines ~379–384):

```js
    function formatSize(bytes) {
      if (bytes == null) return '';
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
      return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    }
```

Then add `ARCHIVE_TYPES`, `isArchivePath`, and `browseArchive` immediately after `formatDate` (after line ~389):

```js
    const ARCHIVE_TYPES = new Set(['zip','tar','tgz','tbz2','gz','bz2','7z','rar']);

    function isArchivePath(filePath) {
      const ext = (filePath || '').split('.').pop().toLowerCase();
      return ARCHIVE_TYPES.has(ext);
    }

    const _browsePanels = {};  // path → panel element

    async function browseArchive(path, btn) {
      // Toggle: close if already open
      if (_browsePanels[path]) {
        _browsePanels[path].remove();
        delete _browsePanels[path];
        btn.textContent = 'Browse \u25ba';
        return;
      }
      btn.textContent = 'Browse \u25bc';

      const panel = document.createElement('div');
      panel.className = 'archive-browse-panel';
      panel.innerHTML = `<span style="color:var(--c-label-dim);">Loading\u2026</span>`;
      btn.closest('.search-result').after(panel);
      _browsePanels[path] = panel;

      let data;
      try {
        data = await api(`/catalog/archive?path=${encodeURIComponent(path)}`);
      } catch (e) {
        panel.innerHTML = `<span class="archive-error">Could not read archive.</span>`;
        return;
      }

      if (data.password_protected) {
        panel.innerHTML = `<span class="archive-error">Password protected \u2014 file listing unavailable.</span>`;
        return;
      }

      const ratio = (data.total_uncompressed_bytes > 0)
        ? Math.round((1 - data.total_compressed_bytes / data.total_uncompressed_bytes) * 100)
        : null;
      const ratioStr = ratio != null ? ` \u00b7 ${ratio}% compression` : '';

      const headerHtml = `<div class="archive-panel-header">${escapeHtml(data.format)} \u00b7 ${data.file_count} files \u00b7 ${formatSize(data.total_uncompressed_bytes)} uncompressed${ratioStr}</div>`;

      const pillsHtml = data.groups.length
        ? `<div class="archive-group-pills">${data.groups.map(g =>
            `<span class="archive-group-pill">${escapeHtml(g.name)} ${g.count}</span>`
          ).join('')}</div>`
        : '';

      const shown = data.entries.slice(0, 500);
      const more = data.file_count - shown.length;
      const rowsHtml = shown.map(e => {
        const encHtml = e.encrypted ? ` <span class="archive-entry-enc">[encrypted]</span>` : '';
        return `<div class="archive-entry-row">
          <span class="archive-entry-name" title="${escapeHtml(e.name)}">${escapeHtml(e.name.slice(0, 60))}</span>
          <span class="archive-entry-size">${formatSize(e.size)}</span>
          <span class="archive-entry-date">${escapeHtml(e.mtime || '')}</span>
          ${encHtml}
        </div>`;
      }).join('');
      const moreHtml = more > 0
        ? `<div class="archive-more">[ ${more} more files not shown ]</div>`
        : '';

      panel.innerHTML = headerHtml + pillsHtml + rowsHtml + moreHtml;
    }
```

- [ ] **Step 3: Update renderResults() to add archive badge + Browse button**

Replace the existing `renderResults` function body. Find the line:

```js
      el.innerHTML = results.map(r => {
```

And replace the entire `el.innerHTML = results.map(r => { ... }).join('');` block with:

```js
      el.innerHTML = results.map(r => {
        const safePath = (r.file_path || '').replace(/"/g, '&quot;');
        const scoreBadge = scoreBadgeHtml(r.score);
        const isArchive = isArchivePath(r.file_path);
        const archiveBadge = isArchive ? `<span class="archive-badge">Archive</span>` : '';
        const browseBtn = isArchive
          ? `<button data-path="${safePath}" onclick="browseArchive(this.dataset.path, this)" class="search-action-btn">Browse &#9654;</button>`
          : '';
        const snippet = isArchive ? '' : `<div class="search-result-snippet">${escapeHtml(r.snippet || r.chunk_text || '')}</div>`;
        return `
        <div class="search-result">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
            <div style="display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap;">
              ${scoreBadge}
              ${archiveBadge}
              <span class="search-result-file" title="${escapeHtml(r.file_path || '')}">${escapeHtml(formatPath(r.file_path))}</span>
            </div>
            <div style="display:flex;gap:4px;flex-shrink:0;">
              <button data-path="${safePath}" onclick="openFile(this.dataset.path)" class="search-action-btn">${t('search.result.open')}</button>
              <button data-path="${safePath}" onclick="openFolder(this.dataset.path)" class="search-action-btn">${t('search.result.folder')}</button>
              <button data-path="${safePath}" onclick="inspectFile(this.dataset.path)" class="search-action-btn">${t('search.result.inspect')}</button>
              ${browseBtn}
            </div>
          </div>
          ${snippet}
        </div>`;
      }).join('');
```

- [ ] **Step 4: Update renderFilenameResults() to add archive badge + Browse button + meta line**

Replace the `el.innerHTML = results.map(r => { ... }).join('');` block in `renderFilenameResults`:

```js
      el.innerHTML = results.map(r => {
        const safePath = (r.file_path || '').replace(/"/g, '&quot;');
        const fname = formatPath(r.file_path);
        const ext = (r.file_type || '').toLowerCase();
        const isArchive = ARCHIVE_TYPES.has(ext) || isArchivePath(r.file_path);
        const size = formatSize(r.file_size);
        const date = formatDate(r.file_modified || r.file_created);
        const notDone = r.status && r.status !== 'COMPLETED';
        const statusHtml = notDone
          ? `<span class="lc-badge ${
              r.status === 'ERROR' ? 'lc-badge-err' : 'lc-badge-pend'
            }">${r.status}</span>`
          : '';
        const archiveBadge = isArchive ? `<span class="archive-badge">Archive</span>` : '';
        // Archive meta from metadata_json (set by extractor: format, file_count, total_size_bytes)
        let archiveMetaHtml = '';
        if (isArchive && r.metadata_json) {
          try {
            const m = typeof r.metadata_json === 'string'
              ? JSON.parse(r.metadata_json) : r.metadata_json;
            if (m && m.format) {
              const parts = [m.format];
              if (m.file_count) parts.push(`${m.file_count} files`);
              if (m.total_size_bytes) parts.push(formatSize(m.total_size_bytes));
              archiveMetaHtml = `<div class="search-result-meta">${escapeHtml(parts.join(' \u00b7 '))}</div>`;
            }
          } catch (e) { /* ignore malformed metadata */ }
        }
        const meta = [ext, size, date].filter(Boolean).join(' \u00b7 ');
        const browseBtn = isArchive
          ? `<button data-path="${safePath}" onclick="browseArchive(this.dataset.path, this)" class="search-action-btn">Browse &#9654;</button>`
          : '';
        return `
        <div class="search-result">
          <div style="display:flex;align-items:start;justify-content:space-between;gap:8px;">
            <div style="min-width:0;">
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                ${archiveBadge}
                <span class="search-result-file">${escapeHtml(fname)}</span>
                ${statusHtml}
              </div>
              <div class="search-result-meta" title="${escapeHtml(r.file_path || '')}">${escapeHtml(r.file_path || '')}</div>
              <div class="search-result-meta">${escapeHtml(meta)}</div>
              ${archiveMetaHtml}
            </div>
            <div style="display:flex;gap:4px;flex-shrink:0;">
              <button data-path="${safePath}" onclick="openFile(this.dataset.path)" class="search-action-btn">${t('search.result.open')}</button>
              <button data-path="${safePath}" onclick="openFolder(this.dataset.path)" class="search-action-btn">${t('search.result.folder')}</button>
              <button data-path="${safePath}" onclick="inspectFile(this.dataset.path)" class="search-action-btn">${t('search.result.inspect')}</button>
              ${browseBtn}
            </div>
          </div>
        </div>`;
      }).join('');
```

- [ ] **Step 5: Manual smoke test**

Start the app:
```
python run.py
```

1. Open `http://localhost:8000/search`
2. Use Filename Search — search for `zip` or `tar`
3. Verify: archive result shows `[ARCHIVE]` badge and `Browse ▶` button
4. Click Browse ▶ — panel expands with file listing
5. Click Browse ▼ — panel collapses
6. Use Content Search — find a document that is inside an indexed archive — verify badge + Browse button appear

- [ ] **Step 6: Commit**

```bash
git add frontend/search.html
git commit -m "feat(ui): archive badge and browse panel in search results"
```

---

## Task 5: Registry test — verify auto-certification covers archive extractor

**Files:**
- Create: `tests/test_registry_certify.py`

### Background

`register_system_kernels()` in `core/registry.py` already auto-certifies `com.docvault.*` kernels. This task adds a regression test so future changes to the registry don't silently break auto-certification for system kernels.

- [ ] **Step 1: Write the tests**

Create `tests/test_registry_certify.py`:

```python
"""
Regression tests for register_system_kernels() auto-certification.
"""
import os
import sys
import sqlite3
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core import manager


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    """An isolated settings.db with ext_registry table."""
    db_path = str(tmp_path / "settings.db")
    # Patch both the source and the name already bound in registry.py at import time
    monkeypatch.setattr('core.manager.get_settings_db_path', lambda: db_path)
    monkeypatch.setattr('core.registry.get_settings_db_path', lambda: db_path)
    manager.init_settings_db()
    return db_path


def _insert_kernel(db_path, kernel_id, status='unverified', certified_at=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO ext_registry
               (kernel_id, module_name, version, file_hash, extensions, status,
                kernel_type, target_type, description, is_enabled, certified_at)
               VALUES (?, ?, '1.0.0', 'abc123', '["zip"]', ?, 'python', 'file', '', 0, ?)""",
            (kernel_id, kernel_id.split('.')[-1], status, certified_at)
        )
        conn.commit()


def _get_status(db_path, kernel_id):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, is_enabled, certified_at FROM ext_registry WHERE kernel_id = ?",
            (kernel_id,)
        ).fetchone()
    return row


def test_register_system_kernels_certifies_uncertified_docvault_kernel(settings_db, monkeypatch):
    """A com.docvault.system.* kernel with certified_at=NULL is auto-certified."""
    _insert_kernel(settings_db, 'com.docvault.system.archive_xray')

    # settings_db fixture already patches get_settings_db_path; patch sync_disk_to_db to no-op
    from core.registry import RegistryManager
    rm = RegistryManager()
    monkeypatch.setattr(rm, 'sync_disk_to_db', lambda: None)

    rm.register_system_kernels()

    status, enabled, certified_at = _get_status(settings_db, 'com.docvault.system.archive_xray')
    assert status == 'certified'
    assert enabled == 1
    assert certified_at is not None


def test_register_system_kernels_is_idempotent(settings_db, monkeypatch):
    """Calling register_system_kernels() twice does not overwrite certified_at."""
    _insert_kernel(settings_db, 'com.docvault.system.archive_xray')

    from core.registry import RegistryManager
    rm = RegistryManager()
    monkeypatch.setattr(rm, 'sync_disk_to_db', lambda: None)

    rm.register_system_kernels()
    _, _, first_certified_at = _get_status(settings_db, 'com.docvault.system.archive_xray')

    rm.register_system_kernels()
    _, _, second_certified_at = _get_status(settings_db, 'com.docvault.system.archive_xray')

    # WHERE certified_at IS NULL excludes already-certified rows → timestamp unchanged
    assert first_certified_at == second_certified_at


def test_register_system_kernels_does_not_certify_third_party(settings_db, monkeypatch):
    """A com.thirdparty.* kernel is NOT auto-certified."""
    _insert_kernel(settings_db, 'com.thirdparty.foo')

    from core.registry import RegistryManager
    rm = RegistryManager()
    monkeypatch.setattr(rm, 'sync_disk_to_db', lambda: None)

    rm.register_system_kernels()

    status, enabled, certified_at = _get_status(settings_db, 'com.thirdparty.foo')
    assert status == 'unverified'
    assert enabled == 0
    assert certified_at is None
```

- [ ] **Step 2: Run the tests**

```
python -m pytest tests/test_registry_certify.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 3: Run the full test suite one final time**

```
python -m pytest --ignore=tests/test_router.py --ignore=tests/test_video_extractor.py -q
```

Expected: no new failures vs. the baseline before this feature.

- [ ] **Step 4: Commit**

```bash
git add tests/test_registry_certify.py
git commit -m "test(registry): regression tests for register_system_kernels auto-certification"
```
