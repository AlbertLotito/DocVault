"""
Archive X-Ray Kernel — shallow header extraction for archive files.
Reads file listings without extracting content to disk.
Supports: zip, tar (gz/bz2/xz), 7z, rar
"""

MANIFEST = {
    "id": "com.docvault.system.archive_xray",
    "version": "1.1.0",
    "name": "Archive X-Ray",
    "extensions": ["zip", "tar", "tgz", "tbz2", "gz", "bz2", "7z", "rar"],
    "requires": [],
}

__description__ = (
    "Indexes archive contents (zip, tar, 7z, rar) by reading their file listings "
    "without extracting to disk. Produces a grouped summary with file counts by type "
    "and a full file listing with top-level README files prioritised."
)

import datetime
import os
import tarfile
from collections import Counter, defaultdict
from core.archive_reader import read_entries, _classify, _GROUP_ORDER
from core.extractors.base import ExtractorContext


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
# Standalone compressed files (.gz / .bz2 that are not tar archives)
# ---------------------------------------------------------------------------

# Cap on indexed text from one compressed file, so a huge log can't flood the
# index (cf. the 326 MB LocationHistory.json that was 11% of the FTS index).
_MAX_STANDALONE_TEXT = 5_000_000

_COMPRESSORS = {'.gz': ('GZIP', 'gzip'), '.bz2': ('BZIP2', 'bz2')}


def _looks_like_text(sample: bytes) -> bool:
    if not sample or b'\x00' in sample:
        return not sample
    try:
        text = sample.decode('utf-8')
    except UnicodeDecodeError:
        text = sample.decode('cp1252', errors='replace')
    printable = sum(c.isprintable() or c in '\t\r\n' for c in text)
    return printable / len(text) > 0.95


def _standalone(file_path: str, archive_name: str) -> tuple:
    """One compressed file: index its text (capped), or describe a binary payload."""
    import importlib
    ext = os.path.splitext(archive_name)[1].lower()
    if ext not in _COMPRESSORS:
        return None, "Standalone compressed file — not a tar archive", None
    fmt, module = _COMPRESSORS[ext]
    inner_name = archive_name[:-len(ext)] or archive_name

    try:
        opener = importlib.import_module(module).open
        with opener(file_path, 'rb') as f:
            data = f.read(_MAX_STANDALONE_TEXT + 1)
            truncated = len(data) > _MAX_STANDALONE_TEXT
            data = data[:_MAX_STANDALONE_TEXT]
            size = len(data) + (sum(len(c) for c in iter(lambda: f.read(1 << 20), b'')) if truncated else 0)
    except (OSError, EOFError, ValueError) as e:
        return None, f"Could not decompress {fmt.lower()} file: {e}", None

    meta = {'format': fmt, 'inner_name': inner_name, 'uncompressed_size': size,
            'compressed_size': os.path.getsize(file_path), 'truncated': truncated}
    header = f"Compressed file: {archive_name} ({fmt}) — contains {inner_name} ({_fmt_size(size)})"

    if _looks_like_text(data[:8192]):
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('cp1252', errors='replace')
        note = f"\n[ truncated after {_fmt_size(_MAX_STANDALONE_TEXT)} ]" if truncated else ''
        return f"{header}\n\n{text}{note}", None, meta
    return f"{header}\nBinary content — not indexed.", None, meta


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    archive_name = os.path.basename(file_path)

    try:
        try:
            entries, total_compressed, fmt = read_entries(file_path)
        except tarfile.TarError:
            return _standalone(file_path, archive_name)

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
