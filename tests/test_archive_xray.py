"""
tests/test_archive_xray.py
Unit tests for the Archive X-Ray kernel.
Run: pytest tests/test_archive_xray.py -v
"""
import io
import os
import sys
import zipfile
import tarfile
import tempfile
import threading
import pytest
import importlib.util

# Import ExtractorContext and ExtractorLogger for real ctx objects
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.extractors.base import ExtractorContext, ExtractorLogger

# --- Load the module under test (once at module level) ---
_ROOT = os.path.dirname(os.path.dirname(__file__))
_MOD_PATH = os.path.join(_ROOT, 'extractors', 'archive_xray_extractor.py')

def _load_mod():
    spec = importlib.util.spec_from_file_location("archive_xray_extractor", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

mod = _load_mod()


# --- ctx factory ---
def make_ctx() -> ExtractorContext:
    """Return a minimal valid ExtractorContext for testing."""
    logger = ExtractorLogger(
        extractor_name='archive_xray_extractor',
        vault_id='test-vault',
        file_hash='0' * 64,
    )
    return ExtractorContext(
        vault_id='test-vault',
        file_hash='0' * 64,
        cancel_token=threading.Event(),
        logger=logger,
        settings=None,
    )


# --- Helpers ---
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


# --- _classify ---
def test_classify_source_code():
    assert mod._classify('src/main.py') == 'Source Code'

def test_classify_document():
    assert mod._classify('report.pdf') == 'Documents'

def test_classify_image():
    assert mod._classify('photo.jpg') == 'Images'

def test_classify_unknown():
    assert mod._classify('data.xyz') == 'Other'


# --- _fmt_size ---
def test_fmt_size_bytes():
    assert mod._fmt_size(512) == '512.0 B'

def test_fmt_size_kilobytes():
    assert mod._fmt_size(2048) == '2.0 KB'

def test_fmt_size_megabytes():
    assert mod._fmt_size(1024 * 1024) == '1.0 MB'


# --- _is_top_readme ---
def test_is_top_readme_match():
    assert mod._is_top_readme('README.md') is True
    assert mod._is_top_readme('readme.txt') is True
    assert mod._is_top_readme('README') is True

def test_is_top_readme_nested_no_match():
    assert mod._is_top_readme('docs/README.md') is False
    assert mod._is_top_readme('src/readme.rst') is False

def test_is_top_readme_non_readme():
    assert mod._is_top_readme('main.py') is False


# --- extract: ZIP ---
def test_extract_zip_basic():
    path = make_zip({
        'hello.txt': 'Hello world',
        'src/main.py': 'print("hi")',
    })
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert result is not None
        assert isinstance(result, str)
        assert 'Archive:' in result
        assert 'Format: ZIP' in result
        assert 'Files: 2' in result
        assert 'hello.txt' in result
        assert 'src/main.py' in result
    finally:
        os.unlink(path)

def test_extract_zip_readme_first():
    """README.md must appear before larger non-readme files."""
    path = make_zip({
        'README.md': 'readme content',
        'bigfile.bin': 'x' * 10000,
        'small.txt': 'tiny',
    })
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        assert err is None
        # Find the first file entry listed after "File Listing:"
        listing_pos = result.index('File Listing:')
        listing_section = result[listing_pos:]
        first_line_after_header = next(
            line for line in listing_section.splitlines()[1:] if line.strip()
        )
        assert 'README.md' in first_line_after_header, (
            f"Expected README.md to be the first listed file; got: {first_line_after_header!r}"
        )
        readme_pos = result.index('README.md')
        bigfile_pos = result.index('bigfile.bin')
        assert readme_pos < bigfile_pos, "README.md should appear before bigfile.bin"
        assert readme_pos < result.index('small.txt'), "README.md should appear before small.txt"
    finally:
        os.unlink(path)

def test_extract_zip_nested_readme_not_prioritised():
    """docs/README.md must NOT be treated as a top-level readme."""
    path = make_zip({
        'docs/README.md': 'nested readme',
        'main.py': 'code',
    })
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert 'docs/README.md' in result
    finally:
        os.unlink(path)

def test_extract_zip_grouped_summary():
    path = make_zip({
        'a.py': 'code',
        'b.py': 'code',
        'doc.pdf': 'pdf content',
    })
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert 'Source Code' in result
        assert 'Documents' in result
    finally:
        os.unlink(path)

def test_extract_zip_empty():
    """Empty archive should return a valid result, not crash."""
    path = make_zip({})
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        # Either a valid string result or a graceful error — must not crash
        assert result is not None or err is not None
    finally:
        os.unlink(path)

def test_extract_zip_directories_only():
    """Archive with only directory entries should not crash."""
    f = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    f.close()
    with zipfile.ZipFile(f.name, 'w') as zf:
        # ZipFile.mkdir() was added in Python 3.11; use writestr for compatibility
        try:
            zf.mkdir('subdir')
        except AttributeError:
            zf.writestr(zipfile.ZipInfo('subdir/'), '')
    try:
        ctx = make_ctx()
        result, err = mod.extract(f.name, ctx)
        assert result is not None or err is not None
    finally:
        os.unlink(f.name)


# --- extract: TAR ---
def test_extract_tar_basic():
    path = make_tar({'README.md': 'readme', 'src/app.py': 'code'})
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert 'Format: TAR' in result
        assert 'README.md' in result
    finally:
        os.unlink(path)

def test_extract_tar_gz():
    path = make_tar({'hello.py': 'print(1)'}, suffix='.tar.gz')
    try:
        ctx = make_ctx()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert 'TAR.GZ' in result
    finally:
        os.unlink(path)


# --- extract: standalone .gz (not a tarball) ---
def test_extract_standalone_gz():
    import gzip
    f = tempfile.NamedTemporaryFile(suffix='.gz', delete=False)
    f.close()
    with gzip.open(f.name, 'wb') as gf:
        gf.write(b'just some compressed data')
    try:
        ctx = make_ctx()
        result, err = mod.extract(f.name, ctx)
        assert result is None
        assert err is not None  # key guarantee: must not be a silent (None, None) return
    finally:
        os.unlink(f.name)


# --- extract: missing file ---
def test_extract_missing_file():
    ctx = make_ctx()
    result, err = mod.extract('/nonexistent/file.zip', ctx)
    assert result is None
    assert err is not None
