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
