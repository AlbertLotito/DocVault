import os
import hashlib
from core import manager


def _sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def ingest(directory, db_path):
    """
    Walk directory, hash each file, insert new tasks.
    Returns count of newly added files.
    """
    added = 0
    for root, _, files in os.walk(directory):
        for name in files:
            file_path = os.path.normpath(os.path.join(root, name))
            ext = os.path.splitext(name)[1].lstrip('.').lower()
            try:
                file_hash = _sha256(file_path)
                if not manager.hash_exists(db_path, file_hash):
                    manager.insert_task(db_path, file_hash, file_path, ext)
                    added += 1
                    print(f"  Added: {name}")
            except (OSError, PermissionError) as e:
                print(f"  Skipped {name}: {e}")
    print(f"Ingestion complete. Added {added} new file(s).")
    return added
