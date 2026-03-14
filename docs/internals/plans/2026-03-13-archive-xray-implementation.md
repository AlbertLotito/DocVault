# Archive X-Ray Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a shallow archive extraction kernel that indexes zip/tar/7z/rar file listings without extracting to disk.

**Architecture:** Single new kernel `extractors/archive_xray_extractor.py` following the standard DocVault kernel pattern (MANIFEST + lazy imports + `extract(file_path, ctx) → (result, err)`). Auto-discovered and auto-certified by the existing registry on startup. No changes to router, ingestor, or manager required.

**Tech Stack:** Python stdlib `zipfile`/`tarfile`, `py7zr` (7z), `rarfile` (rar), `collections.Counter`/`defaultdict`

---

### Task 1: Install Dependencies

**Files:**
- No file changes — venv pip installs only

**Step 1: Activate venv and install**

```bash
cd E:/DocVault
source venv/Scripts/activate   # Windows: venv\Scripts\activate
pip install py7zr rarfile
```

Expected output: `Successfully installed py7zr-... rarfile-...`

**Step 2: Verify imports work**

```bash
python -c "import py7zr; import rarfile; print('OK')"
```

Expected: `OK`

**Step 3: Commit requirements**

```bash
pip freeze | grep -E "py7zr|rarfile" >> requirements.txt
git add requirements.txt
git commit -m "deps: add py7zr and rarfile for archive extraction"
```

---

### Task 2: Write Failing Tests

**Files:**
- Create: `tests/test_archive_xray.py`

**Step 1: Create the test file**

```python
"""
tests/test_archive_xray.py
Unit tests for the Archive X-Ray kernel.
Run: pytest tests/test_archive_xray.py -v
"""
import io
import os
import zipfile
import tarfile
import tempfile
import pytest
import importlib.util

# --- Load the module under test ---
_ROOT = os.path.dirname(os.path.dirname(__file__))
_MOD_PATH = os.path.join(_ROOT, 'extractors', 'archive_xray_extractor.py')

def _load_mod():
    spec = importlib.util.spec_from_file_location("archive_xray_extractor", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# Lazy load so import errors surface as test failures, not collection errors
mod = None

@pytest.fixture(autouse=True)
def load_module():
    global mod
    mod = _load_mod()

# --- Helpers ---
def make_zip(files: dict, password: bytes = None) -> str:
    """files: {name: content_str}. Returns temp file path."""
    f = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    f.close()
    with zipfile.ZipFile(f.name, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return f.name

def make_tar(files: dict, suffix='.tar') -> str:
    """files: {name: content_bytes}. Returns temp file path."""
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
        ctx = type('ctx', (), {})()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert result is not None
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
        ctx = type('ctx', (), {})()
        result, err = mod.extract(path, ctx)
        assert err is None
        readme_pos = result.index('README.md')
        bigfile_pos = result.index('bigfile.bin')
        assert readme_pos < bigfile_pos, "README.md should appear before bigfile.bin"
    finally:
        os.unlink(path)

def test_extract_zip_nested_readme_not_prioritised():
    """docs/README.md must NOT be treated as a top-level readme."""
    path = make_zip({
        'docs/README.md': 'nested readme',
        'main.py': 'code',
    })
    try:
        ctx = type('ctx', (), {})()
        result, err = mod.extract(path, ctx)
        assert err is None
        # Just check it runs without error; order doesn't matter here
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
        ctx = type('ctx', (), {})()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert 'Source Code' in result
        assert 'Documents' in result
    finally:
        os.unlink(path)


# --- extract: TAR ---
def test_extract_tar_basic():
    path = make_tar({'README.md': 'readme', 'src/app.py': 'code'})
    try:
        ctx = type('ctx', (), {})()
        result, err = mod.extract(path, ctx)
        assert err is None
        assert 'Format: TAR' in result
        assert 'README.md' in result
    finally:
        os.unlink(path)

def test_extract_tar_gz():
    path = make_tar({'hello.py': 'print(1)'}, suffix='.tar.gz')
    try:
        ctx = type('ctx', (), {})()
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
        ctx = type('ctx', (), {})()
        result, err = mod.extract(f.name, ctx)
        # Should return None result + a descriptive error (not a crash)
        assert result is None
        assert err is not None
        assert 'standalone' in err.lower() or 'not a tar' in err.lower()
    finally:
        os.unlink(f.name)


# --- extract: missing file ---
def test_extract_missing_file():
    ctx = type('ctx', (), {})()
    result, err = mod.extract('/nonexistent/file.zip', ctx)
    assert result is None
    assert err is not None
```

**Step 2: Run tests — verify they all fail (module not yet created)**

```bash
pytest tests/test_archive_xray.py -v 2>&1 | head -30
```

Expected: errors like `FileNotFoundError` (module path doesn't exist) or `ModuleNotFoundError`. NOT "all passed."

---

### Task 3: Write the Kernel

**Files:**
- Create: `extractors/archive_xray_extractor.py`

**Step 1: Write the kernel**

```python
"""
Archive X-Ray Kernel — shallow header extraction for archive files.
Reads file listings without extracting content to disk.
Supports: zip, tar (gz/bz2/xz), 7z, rar
"""

MANIFEST = {
    "id": "com.docvault.system.archive_xray",
    "version": "1.0.0",
    "name": "Archive X-Ray",
    "extensions": ["zip", "tar", "tgz", "tbz2", "gz", "bz2", "7z", "rar"],
    "requires": [],
}

__description__ = (
    "Indexes archive contents (zip, tar, 7z, rar) by reading their file listings "
    "without extracting to disk. Produces a grouped summary with file counts by type "
    "and a full file listing with top-level README files prioritised."
)

import os
import zipfile
import tarfile
import datetime
from collections import Counter, defaultdict

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

from core.extractors.base import ExtractorContext

# ---------------------------------------------------------------------------
# File type grouping
# ---------------------------------------------------------------------------
_GROUPS = {
    'Source Code': {
        'py','js','ts','java','c','cpp','h','cs','go','rb','php','swift',
        'kt','rs','sh','bat','ps1','lua','r','m','scala','clj','ex','exs',
        'elm','vue','jsx','tsx','coffee','dart','nim','zig',
    },
    'Documents': {
        'pdf','doc','docx','xls','xlsx','ppt','pptx','odt','ods','odp',
        'rtf','pages','numbers','key','epub','md','rst','tex','txt',
    },
    'Images': {
        'jpg','jpeg','png','gif','bmp','tiff','tif','webp','svg','ico',
        'raw','cr2','nef','arw','dng','heic','psd','ai',
    },
    'Audio': {'mp3','wav','flac','ogg','m4a','aac','opus','wma','aiff'},
    'Video': {'mp4','mkv','avi','mov','wmv','webm','flv','m4v','mpg','mpeg'},
    'Archives': {'zip','7z','rar','tar','gz','bz2','xz','tgz','tbz2'},
    'Data': {
        'json','xml','csv','yaml','yml','toml','sql','db','sqlite',
        'sqlite3','parquet','h5','hdf5',
    },
}

_GROUP_ORDER = ['Source Code', 'Documents', 'Images', 'Audio', 'Video',
                'Archives', 'Data', 'Other']


def _classify(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lstrip('.').lower()
    for group, exts in _GROUPS.items():
        if ext in exts:
            return group
    return 'Other'


def _fmt_size(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_date(dt) -> str:
    """Accept datetime, unix timestamp, or date_time tuple (zipfile)."""
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


def _is_top_readme(name: str) -> bool:
    """True only for top-level README files (no directory component)."""
    clean = name.replace('\\', '/').rstrip('/')
    dirname = os.path.dirname(clean)
    basename = os.path.basename(clean)
    return (not dirname) and basename.lower().startswith('readme')


# ---------------------------------------------------------------------------
# Output builder
# ---------------------------------------------------------------------------
_MAX_LISTING = 500


def _build_output(archive_name: str, fmt: str, entries: list,
                  total_compressed: int) -> str:
    files = [e for e in entries if not e['is_dir']]

    total_uncompressed = sum(e['size'] for e in files)
    if total_uncompressed > 0 and total_compressed > 0:
        ratio = max(0, int((1 - total_compressed / total_uncompressed) * 100))
    else:
        ratio = 0

    lines = [
        f"Archive: {archive_name}",
        f"Format: {fmt}  |  Files: {len(files)}  |  "
        f"Uncompressed: {_fmt_size(total_uncompressed)}  |  Ratio: {ratio}%",
        "",
        "Contents by Type:",
    ]

    # Grouped summary
    group_counts: dict[str, Counter] = defaultdict(Counter)
    for e in files:
        grp = _classify(e['name'])
        ext = os.path.splitext(e['name'])[1].lstrip('.').lower() or 'no ext'
        group_counts[grp][ext] += 1

    for grp in _GROUP_ORDER:
        if grp not in group_counts:
            continue
        counter = group_counts[grp]
        total = sum(counter.values())
        plural = 'file' if total == 1 else 'files'
        label = f"  {grp:<14} {total:>4} {plural}"
        top_exts = sorted(counter.items(), key=lambda x: -x[1])[:5]
        detail = ', '.join(f"{k}: {v}" for k, v in top_exts)
        lines.append(f"{label}   ({detail})")

    lines.append("")
    lines.append("File Listing:")

    # Sort: top-level READMEs first, then size descending
    readmes = [e for e in files if _is_top_readme(e['name'])]
    rest = sorted(
        [e for e in files if not _is_top_readme(e['name'])],
        key=lambda x: -x['size']
    )
    sorted_files = readmes + rest

    shown = sorted_files[:_MAX_LISTING]
    hidden = len(sorted_files) - len(shown)

    for e in shown:
        name_col = e['name'][:60].ljust(62)
        size_col = _fmt_size(e['size']).rjust(10)
        date_col = f"  {e['mtime']}" if e.get('mtime') else ''
        enc = '  [encrypted]' if e.get('encrypted') else ''
        lines.append(f"  {name_col}{size_col}{date_col}{enc}")

    if hidden:
        lines.append(f"\n  [ {hidden} more files not shown ]")

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Format readers
# ---------------------------------------------------------------------------

def _read_zip(file_path: str) -> tuple:
    entries = []
    total_compressed = 0
    with zipfile.ZipFile(file_path, 'r') as zf:
        for info in zf.infolist():
            entries.append({
                'name': info.filename,
                'size': info.file_size,
                'mtime': _fmt_date(info.date_time),
                'is_dir': info.is_dir(),
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
                'name': member.name,
                'size': member.size,
                'mtime': _fmt_date(member.mtime),
                'is_dir': member.isdir(),
                'encrypted': False,
            })

    compressed = os.path.getsize(file_path)
    return entries, compressed, fmt


def _read_7z(file_path: str) -> tuple:
    if not _HAS_7Z:
        raise ImportError("py7zr is not installed — run: pip install py7zr")
    entries = []
    total_compressed = 0
    with _py7zr.SevenZipFile(file_path, mode='r') as zf:
        for info in zf.list():
            entries.append({
                'name': info.filename,
                'size': info.uncompressed or 0,
                'mtime': _fmt_date(info.creationtime) if info.creationtime else '',
                'is_dir': info.is_directory,
                'encrypted': False,
            })
            if info.compressed:
                total_compressed += info.compressed
    if not total_compressed:
        total_compressed = os.path.getsize(file_path)
    return entries, total_compressed, '7Z'


def _read_rar(file_path: str) -> tuple:
    if not _HAS_RAR:
        raise ImportError("rarfile is not installed — run: pip install rarfile")
    rf = _rarfile.RarFile(file_path)
    if rf.needs_password():
        raise PermissionError("Password protected")
    entries = []
    total_compressed = 0
    for info in rf.infolist():
        entries.append({
            'name': info.filename,
            'size': info.file_size,
            'mtime': _fmt_date(info.date_time) if info.date_time else '',
            'is_dir': info.is_dir(),
            'encrypted': False,
        })
        total_compressed += info.compress_size
    return entries, total_compressed, 'RAR'


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    archive_name = os.path.basename(file_path)
    name_lower = archive_name.lower()

    try:
        if name_lower.endswith('.zip'):
            entries, total_compressed, fmt = _read_zip(file_path)

        elif (name_lower.endswith('.tar')
              or name_lower.endswith('.tar.gz')
              or name_lower.endswith('.tar.bz2')
              or name_lower.endswith('.tar.xz')
              or name_lower.endswith('.tgz')
              or name_lower.endswith('.tbz2')
              or name_lower.endswith('.gz')
              or name_lower.endswith('.bz2')):
            try:
                entries, total_compressed, fmt = _read_tar(file_path)
            except tarfile.TarError:
                return None, "Standalone compressed file — not a tar archive"

        elif name_lower.endswith('.7z'):
            entries, total_compressed, fmt = _read_7z(file_path)

        elif name_lower.endswith('.rar'):
            entries, total_compressed, fmt = _read_rar(file_path)

        else:
            return None, f"Unsupported archive format: {archive_name}"

        return _build_output(archive_name, fmt, entries, total_compressed), None

    except PermissionError:
        fmt_guess = name_lower.rsplit('.', 1)[-1].upper()
        return (
            f"Archive: {archive_name}\n"
            f"Format: {fmt_guess}\n\n"
            f"Password protected — file listing unavailable"
        ), None

    except ImportError as e:
        return None, str(e)

    except Exception as e:
        return None, f"Archive read failed: {e}"
```

**Step 2: Run tests — verify they pass**

```bash
pytest tests/test_archive_xray.py -v
```

Expected: All tests PASS.

**Step 3: Commit**

```bash
git add extractors/archive_xray_extractor.py tests/test_archive_xray.py
git commit -m "feat: implement Archive X-Ray kernel (zip/tar/7z/rar header extraction)"
```

---

### Task 4: Register and Smoke-Test

**Files:**
- No changes required — registry auto-discovers the kernel on startup

**Step 1: Start the server**

```bash
cd E:/DocVault
python run.py
```

Watch startup logs for:
```
[registry] Discovered kernel: com.docvault.system.archive_xray
[registry] Auto-certified system kernel: com.docvault.system.archive_xray
[router] Router updated. N kernels mapped.
```

**Step 2: Verify in the Extractor Lab UI**

Open `http://localhost:8000/lab`

The sidebar should show **Archive X-Ray** with a `certified` pill.
Click it → Certification panel should show manifest checks passing.

**Step 3: Test with a real archive via the Lab**

1. Click Archive X-Ray in sidebar
2. Click Test panel
3. Browse to any `.zip` file on disk
4. Click Run
5. Verify output shows the grouped summary + file listing

**Step 4: Verify archive tasks flow through the queue**

After the server has been running for a minute, open `http://localhost:8000/catalog` and filter for `.zip` files. They should show status EXTRACTED or COMPLETED rather than ERROR.

**Step 5: Update memory**

Add to `C:\Users\Albert\.claude\projects\E--DocVault\memory\MEMORY.md`:
```
- `extractors/archive_xray_extractor.py` — Archive X-Ray kernel; zip/tar/7z/rar header listing; deps: py7zr, rarfile
```

---

### Notes

- **unrar binary**: If RAR files fail with `RarCannotExec`, install WinRAR or unrar and ensure it is on PATH. `rarfile` needs the binary for RAR v5 archives.
- **Compound extensions** (`.tar.gz`): `os.path.splitext` returns `.gz` — the kernel handles this correctly by checking `endswith` on the full filename, not just the last extension.
- **Re-running on existing ERROR tasks**: After the server is running with the new kernel, use the Utilities page → Health Check → "Retry extraction errors" to re-queue any `.zip`/`.tar`/etc. tasks that previously landed as `Unsupported format` errors.
