import os
import hashlib
import datetime
import ctypes
import fnmatch
from core import manager, logger
from core.router import get_priority
from core.settings import settings

# Filenames always skipped regardless of location
_BLOCKLIST = frozenset({
    'desktop.ini', 'thumbs.db', '.ds_store', 'ntuser.dat',
    'ntuser.dat.log', 'ntuser.pol', 'usrclass.dat',
})

# Extensions always skipped — sidecars and system metadata files
_BLOCKED_EXTENSIONS = frozenset({'.nfo'})


def parse_ignore_patterns(csv: str) -> frozenset:
    """Parse a comma-separated pattern string into a frozenset of stripped, non-empty lowercase patterns."""
    return frozenset(p.strip().lower() for p in csv.split(',') if p.strip())


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


def _update_vector_path(file_hash, new_path):
    """Update the stored file_path in the vector store for this hash."""
    try:
        from embeddings.vector_store import VectorStore
        VectorStore().update_path(file_hash, new_path)
    except Exception as e:
        logger.error(f"[vector] Path update failed for {file_hash[:8]}: {e}")


def _update_missing_flags(db_path, vault_id, seen_paths):
    """After a vault walk completes, reconcile file_vault.miss_count against what
    was actually seen on disk this cycle. Flips a task to MISSING once every
    file_vault row for its hash has crossed the configured threshold; restores
    it if any row resets to 0 (the file reappeared). Runs as one connection/one
    transaction per vault -- avoids one write-lock acquisition per file on
    large scans, and skips already-MISSING hashes so miss_count doesn't grow
    forever for files that are left unpurged."""
    if not vault_id:
        return
    threshold = int(settings.get('ingestion:missing_after_scans') or 3)
    BATCH_SIZE = 500

    with manager._connect(db_path) as conn:
        rows = conn.execute(
            "SELECT file_hash, file_path, miss_count FROM file_vault WHERE vault_id = ?",
            (vault_id,)
        ).fetchall()
        if not rows:
            return

        hashes = list({r['file_hash'] for r in rows})
        already_missing = set()
        for i in range(0, len(hashes), BATCH_SIZE):
            batch = hashes[i:i + BATCH_SIZE]
            placeholders = ','.join('?' * len(batch))
            already_missing.update(
                r['file_hash'] for r in conn.execute(
                    f"SELECT file_hash FROM tasks WHERE status = 'MISSING' AND file_hash IN ({placeholders})",
                    batch
                ).fetchall()
            )

        reappeared_hashes = set()
        newly_crossed_hashes = set()

        for row in rows:
            path = os.path.normpath(row['file_path'])
            file_hash = row['file_hash']
            if path in seen_paths:
                if row['miss_count'] > 0:
                    conn.execute(
                        "UPDATE file_vault SET miss_count = 0 WHERE file_hash = ? AND vault_id = ?",
                        (file_hash, vault_id)
                    )
                    if file_hash in already_missing:
                        reappeared_hashes.add(file_hash)
            elif file_hash not in already_missing:
                new_count = row['miss_count'] + 1
                conn.execute(
                    "UPDATE file_vault SET miss_count = ? WHERE file_hash = ? AND vault_id = ?",
                    (new_count, file_hash, vault_id)
                )
                if new_count >= threshold:
                    newly_crossed_hashes.add(file_hash)

        conn.commit()

    for file_hash in newly_crossed_hashes:
        if manager.all_file_vault_rows_missing(db_path, file_hash, threshold):
            manager.flag_task_missing(db_path, file_hash)

    for file_hash in reappeared_hashes:
        manager.restore_task_from_missing(db_path, file_hash)


def ingest(directory, db_path, vault_id=None, vault_row=None):
    """
    Walk directory, hash each file, and:
      - Insert new files with full metadata (size, created, modified).
      - Detect moved/renamed files (same hash, different path) and update
        the path in SQLite and the vector store without re-embedding.
    Returns (added, moved) counts.
    """
    from extractors.image_extractor import _cache_dir
    cache_dir = os.path.normpath(_cache_dir())

    global_exts    = parse_ignore_patterns(settings.get('ingestion:ignore_extensions') or '')
    global_folders = parse_ignore_patterns(settings.get('ingestion:ignore_folders') or '')
    vault_exts     = parse_ignore_patterns((vault_row or {}).get('ignore_extensions', ''))
    vault_folders  = parse_ignore_patterns((vault_row or {}).get('ignore_folders', ''))
    ignore_exts    = global_exts | vault_exts
    ignore_folders_set = global_folders | vault_folders

    added = 0
    moved = 0
    seen_paths = set()

    for root, dirs, files in os.walk(directory):
        # ── Folder pruning (must be first — prunes before any stat/hash work) ─
        if ignore_folders_set:
            pruned = [
                d for d in dirs
                if any(fnmatch.fnmatch(d.lower(), pat) for pat in ignore_folders_set)
            ]
            for d in pruned:
                for sub_root, _, sub_files in os.walk(os.path.join(root, d)):
                    for sub_name in sub_files:
                        seen_paths.add(os.path.normpath(os.path.join(sub_root, sub_name)))
            dirs[:] = [d for d in dirs if d not in pruned]

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
            file_path = os.path.normpath(os.path.join(root, name))
            seen_paths.add(file_path)
            if name.lower() in _BLOCKLIST:
                continue
            if _is_hidden_or_system(file_path):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in _BLOCKED_EXTENSIONS:
                continue
            ext = ext.lstrip('.')
            if ext in ignore_exts or ('.' + ext) in ignore_exts:
                continue
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

                    elif os.path.normpath(vault_path) != file_path and os.path.exists(vault_path):
                        # Same hash at a different path, but the old path still exists on
                        # disk too — a content duplicate (e.g. hash-identical Office
                        # boilerplate across many .doc/.docx files), not a real move.
                        # Leave the registered canonical path alone.
                        pass

                    elif os.path.normpath(vault_path) != file_path:
                        # File has moved within this vault — update vault-specific path
                        manager.upsert_file_vault(db_path, file_hash, vault_id, file_path)
                        if existing['vault_id'] == vault_id:
                            # Origin vault: also update canonical path in tasks + vector store
                            manager.update_task_path(db_path, file_hash, file_path)
                            _update_vector_path(file_hash, file_path)
                            moved += 1
                            logger.info(f"  Moved: {existing['file_path']} → {file_path}")

                    # else: known file at known vault path — nothing to do

            except Exception as e:
                logger.warn(f"  Skipped {name}: {e}")

    _update_missing_flags(db_path, vault_id, seen_paths)

    logger.info(f"Ingestion complete. Added {added}, moved {moved} file(s).")
    return added, moved
