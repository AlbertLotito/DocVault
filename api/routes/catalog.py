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
                 vault_id: str = None,
                 limit: int = 50, offset: int = 0,
                 sort_by: str = 'last_update', sort_order: str = 'DESC'):
    return manager.list_tasks(get_db(), status=status,
                               file_type=file_type, vault_id=vault_id,
                               limit=limit, offset=offset,
                               sort_by=sort_by, sort_order=sort_order)


@router.get("/catalog/inspect")
def inspect_file(path: str = Query(...)):
    """Return full DB record + Qdrant chunks + extracted images for a file path."""
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

    # Qdrant chunks
    chunks = []
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http import models as qm
        from core.settings import settings
        client = QdrantClient(host=settings.get('qdrant:host'), port=int(settings.get('qdrant:port')))
        results, _ = client.scroll(
            collection_name='docvault',
            scroll_filter=qm.Filter(must=[
                qm.FieldCondition(key='file_hash', match=qm.MatchValue(value=task['file_hash']))
            ]),
            limit=200,
            with_payload=True,
            with_vectors=False,
        )
        chunks = sorted(
            [{'index': p.payload.get('chunk_index', i), 'text': p.payload.get('chunk_text', '')}
             for i, p in enumerate(results)],
            key=lambda x: x['index']
        )
    except Exception:
        pass

    return {'task': task, 'chunks': chunks, 'images': images}


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
