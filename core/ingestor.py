import os
import hashlib
import datetime
import ctypes
from core import manager, logger
from core.router import get_priority

# Filenames always skipped regardless of location
_BLOCKLIST = frozenset({
    'desktop.ini', 'thumbs.db', '.ds_store', 'ntuser.dat',
    'ntuser.dat.log', 'ntuser.pol', 'usrclass.dat',
})

_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4


def _is_hidden_or_system(file_path: str) -> bool:
    """Return True if file has the Windows hidden or system attribute."""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(file_path)
        return attrs != -1 and bool(attrs & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM))
    except Exception:
        return False


def _sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _stat(file_path):
    """Return (size, created_iso, modified_iso) from os.stat()."""
    s = os.stat(file_path)
    created  = datetime.datetime.fromtimestamp(s.st_ctime).isoformat()
    modified = datetime.datetime.fromtimestamp(s.st_mtime).isoformat()
    return s.st_size, created, modified


def _update_qdrant_path(file_hash, new_path):
    """Push the new file_path into every Qdrant vector payload for this hash."""
    try:
        from embeddings.vector_store import VectorStore
        from core.settings import settings
        vs = VectorStore(
            host=settings.get('qdrant:host'),
            port=int(settings.get('qdrant:port')),
            collection='docvault',
        )
        vs.update_path(file_hash, new_path)
    except Exception as e:
        logger.error(f"[qdrant] Path update failed for {file_hash[:8]}: {e}")


def ingest(directory, db_path, vault_id=None):
    """
    Walk directory, hash each file, and:
      - Insert new files with full metadata (size, created, modified).
      - Detect moved/renamed files (same hash, different path) and update
        the path in SQLite and Qdrant without re-embedding.
    Returns (added, moved) counts.
    """
    from core.settings import settings
    from extractors.image_extractor import _cache_dir
    cache_dir = os.path.normpath(_cache_dir())

    added = 0
    moved = 0

    for root, dirs, files in os.walk(directory):
        # Skip the cache directory if it happens to live inside the scan tree
        if os.path.normpath(root).startswith(cache_dir):
            dirs.clear()
            continue
        for name in files:
            if name.lower() in _BLOCKLIST:
                continue
            file_path = os.path.normpath(os.path.join(root, name))
            if _is_hidden_or_system(file_path):
                continue
            ext = os.path.splitext(name)[1].lstrip('.').lower()
            try:
                file_hash = _sha256(file_path)
                existing  = manager.get_task(db_path, file_hash)

                if existing is None:
                    # Brand-new file
                    size, created, modified = _stat(file_path)
                    priority = get_priority(ext)
                    manager.insert_task(
                        db_path, file_hash, file_path, ext, priority,
                        file_size=size, file_created=created, file_modified=modified,
                        vault_id=vault_id,
                    )
                    added += 1
                    logger.info(f"  Added: {name} (priority: {priority})")

                elif os.path.normpath(existing['file_path']) != file_path:
                    # Same content, new location — file was moved or renamed
                    manager.update_task_path(db_path, file_hash, file_path)
                    _update_qdrant_path(file_hash, file_path)
                    moved += 1
                    logger.info(f"  Moved: {existing['file_path']} → {file_path}")

                # else: known file at known path — nothing to do

            except (OSError, PermissionError) as e:
                logger.warn(f"  Skipped {name}: {e}")

    logger.info(f"Ingestion complete. Added {added}, moved {moved} file(s).")
    return added, moved
