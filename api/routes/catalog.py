from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from core import manager
import sqlite3

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


@router.get("/stats")
def get_stats():
    return manager.get_stats(get_db())


@router.get("/catalog")
def list_catalog(status: str = None, file_type: str = None,
                 filename: str = None, vault_id: str = None,
                 limit: int = 50, offset: int = 0,
                 sort_by: str = 'last_update', sort_order: str = 'DESC'):
    return manager.list_tasks(get_db(), status=status,
                               file_type=file_type, filename=filename,
                               vault_id=vault_id,
                               limit=limit, offset=offset,
                               sort_by=sort_by, sort_order=sort_order)


@router.get("/catalog/inspect")
def inspect_file(path: str = Query(...)):
    """Return full DB record + vector store chunks + extracted images for a file path."""
    db = get_db()

    # Look up task by file_path
    with sqlite3.connect(db, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tasks WHERE file_path = ?", (path,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="File not found in database")
        task = dict(row)

        # Extracted images linked to this file's hash
        img_rows = conn.execute(
            "SELECT file_path, page_num, image_index, width, height FROM extracted_images WHERE source_hash = ? ORDER BY page_num, image_index",
            (task['file_hash'],)
        ).fetchall()
        images = [dict(r) for r in img_rows]

    chunks = []
    try:
        from embeddings.vector_store import VectorStore
        chunks = VectorStore().get_chunks_by_hash(task['file_hash'])
    except Exception:
        pass

    return {'task': task, 'chunks': chunks, 'images': images}


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


@router.get("/catalog/{file_hash}/text")
def get_catalog_text(file_hash: str):
    """Return the full extracted text for a document (used by the inline span viewer)."""
    db = get_db()
    row = manager.get_task(db, file_hash)
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    # Fetch from extracted_texts table
    try:
        with sqlite3.connect(db, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            et_row = conn.execute(
                "SELECT extracted_text FROM extracted_texts WHERE file_hash = ?", (file_hash,)
            ).fetchone()
    except Exception:
        et_row = None
    if not et_row or not et_row['extracted_text']:
        raise HTTPException(status_code=404, detail="Extracted text not found")
    return {"extracted_text": et_row['extracted_text']}


@router.get("/catalog/{file_hash}")
def get_catalog_item(file_hash: str):
    task = manager.get_task(get_db(), file_hash)
    if not task:
        raise HTTPException(status_code=404, detail="File not found")
    return task


@router.post("/catalog/{file_hash}/reprocess")
def reprocess_item(file_hash: str):
    task = manager.get_task(get_db(), file_hash)
    if not task:
        raise HTTPException(status_code=404, detail="File not found")
    manager.reprocess_task(get_db(), file_hash)
    return {"status": "ok", "file_hash": file_hash}
