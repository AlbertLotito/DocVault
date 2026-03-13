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


def _fmt_size(n: int | float) -> str:
    val = float(n)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} PB"


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
    group_counts: dict = defaultdict(Counter)
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
    try:
        with _py7zr.SevenZipFile(file_path, mode='r') as zf:
            if zf.needs_password():
                raise PermissionError("Password protected")
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
                return None, "Standalone compressed file — not a tar archive", None

        elif name_lower.endswith('.7z'):
            entries, total_compressed, fmt = _read_7z(file_path)

        elif name_lower.endswith('.rar'):
            entries, total_compressed, fmt = _read_rar(file_path)

        else:
            return None, f"Unsupported archive format: {archive_name}", None

        meta = {
            'format': fmt,
            'file_count': len([e for e in entries if not e['is_dir']]),
            'total_size_bytes': sum(e['size'] for e in entries if not e['is_dir']),
        }
        return _build_output(archive_name, fmt, entries, total_compressed), None, meta

    except PermissionError:
        fmt_guess = name_lower.rsplit('.', 1)[-1].upper()
        return (
            f"Archive: {archive_name}\n"
            f"Format: {fmt_guess}\n\n"
            f"Password protected — file listing unavailable"
        ), None, {'format': fmt_guess, 'password_protected': True}

    except ImportError as e:
        return None, str(e), None

    except Exception as e:
        return None, f"Archive read failed: {e}", None
