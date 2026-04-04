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

# Extensions always skipped — sidecars and system metadata files
_BLOCKED_EXTENSIONS = frozenset({'.nfo'})

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
        # ── Part 1: Folder Intelligence ──────────────────────────────────────
        # Check if the current folder itself should be a task unit
        root_norm = os.path.normpath(root)
        
        # We only consider folders that haven't been 'moved' or renamed
        # Folder ID is derived from the normalized path
        folder_hash = f"DIR_{hashlib.md5(root_norm.encode()).hexdigest()}"
        
        # Simple detection: if >50% of files share an extension that has a folder extractor
        from core.router import get_folder_extractors
        ext_counts = {}
        for f in files:
            e = os.path.splitext(f)[1].lstrip('.').lower()
            if e: ext_counts[e] = ext_counts.get(e, 0) + 1
        
        for ext, count in ext_counts.items():
            if count >= len(files) * 0.5 and get_folder_extractors(ext):
                # This folder is a candidate for collection intelligence
                try:
                    if not manager.get_task(db_path, folder_hash):
                        manager.insert_task(
                            db_path, folder_hash, root_norm, f"directory/{ext}", 5,
                            vault_id=vault_id
                        )
                        logger.info(f"  Added Directory Unit: {os.path.basename(root)} (type: {ext})")
                        added += 1
                    manager.upsert_file_vault(db_path, folder_hash, vault_id, root_norm)
                except Exception as e:
                    logger.warn(f"  Skipped directory {os.path.basename(root)}: {e}")
                break

        # ── Part 2: File Intelligence ────────────────────────────────────────
        # Skip the cache directory if it happens to live inside the scan tree
        if root_norm.startswith(cache_dir):
            dirs.clear()
            continue
        for name in files:
            if name.lower() in _BLOCKLIST:
                continue
            file_path = os.path.normpath(os.path.join(root, name))
            if _is_hidden_or_system(file_path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in _BLOCKED_EXTENSIONS:
                continue
            ext = ext.lstrip('.')
            try:
                file_hash = _sha256(file_path)
                existing  = manager.get_task(db_path, file_hash)

                if existing is None:
                    # Brand-new file — create task and register vault membership
                    size, created, modified = _stat(file_path)
                    priority = get_priority(ext)
                    manager.insert_task(
                        db_path, file_hash, file_path, ext, priority,
                        file_size=size, file_created=created, file_modified=modified,
                        vault_id=vault_id,
                    )
                    manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                    added += 1
                    logger.info(f"  Added: {name} (priority: {priority})")

                else:
                    vault_path = manager.get_file_vault_path(db_path, file_hash, vault_id)

                    if vault_path is None:
                        # File known globally but first time this vault sees it
                        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                        logger.info(f"  Registered in vault: {name}")

                    elif os.path.normpath(vault_path) != file_path:
                        # File has moved within this vault — update vault-specific path
                        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                        if existing['vault_id'] == vault_id:
                            # Origin vault: also update canonical path in tasks + Qdrant
                            manager.update_task_path(db_path, file_hash, file_path)
                            _update_qdrant_path(file_hash, file_path)
                            moved += 1
                            logger.info(f"  Moved: {existing['file_path']} → {file_path}")

                    # else: known file at known vault path — nothing to do

            except Exception as e:
                logger.warn(f"  Skipped {name}: {e}")

    logger.info(f"Ingestion complete. Added {added}, moved {moved} file(s).")
    return added, moved
