import os
import subprocess
from fastapi import APIRouter
from pydantic import BaseModel
from utils.qdrant_check import check_qdrant_health
from core import manager
from core.settings import settings
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

router = APIRouter()


@router.get("/utils/qdrant_check")
async def run_qdrant_check():
    """Runs a health check on the Qdrant database."""
    return await check_qdrant_health()


class OpenRequest(BaseModel):
    path: str
    action: str  # 'file' or 'folder'


@router.post("/utils/reindex")
def reindex_all():
    """
    Wipe the Qdrant vector collection and reset all COMPLETED tasks to EXTRACTED
    so the embedding worker re-embeds everything from scratch.
    Extracted text, FTS index, settings, and file metadata are untouched.
    """
    from api.main import DB_PATH

    # 1. Drop and recreate Qdrant collection
    host       = settings.get('qdrant:host')
    port       = int(settings.get('qdrant:port'))
    collection = 'docvault'
    client = QdrantClient(host=host, port=port)
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.create_collection(
        collection_name=collection,
        vectors_config=qdrant_models.VectorParams(
            size=768,
            distance=qdrant_models.Distance.COSINE,
            on_disk=True,
        ),
    )

    # 2. Reset COMPLETED → EXTRACTED in SQLite
    import sqlite3
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='EXTRACTED', worker_id=NULL WHERE status='COMPLETED'"
        )
        reset_count = cur.rowcount

    return {'status': 'ok', 'reset_tasks': reset_count}


@router.get("/utils/gdrive_status")
def gdrive_status():
    """Check whether Google Drive OAuth token exists."""
    from extractors.gdrive_extractor import is_authorized
    return {'authorized': is_authorized()}


@router.post("/utils/gdrive_authorize")
def gdrive_authorize():
    """
    Run the Google Drive OAuth flow (opens a browser window).
    Returns {ok: true} on success or {ok: false, detail: '...'} on failure.
    """
    from extractors.gdrive_extractor import ensure_authorized
    ok, err = ensure_authorized()
    if ok:
        return {'ok': True}
    return {'ok': False, 'detail': err}


@router.post("/utils/clear_logs")
def clear_logs():
    from core.manager import get_logs_db_path, _connect
    with _connect(get_logs_db_path()) as conn:
        conn.executescript("""
            DELETE FROM task_timings;
            DELETE FROM worker_errors;
            DELETE FROM worker_log;
            DELETE FROM extractor_stats;
            DELETE FROM system_stats;
        """)
        conn.commit()
    return {"ok": True}


@router.post("/utils/open_path")
def open_path(req: OpenRequest):
    if not os.path.exists(req.path):
        return {"status": "error", "detail": "Path not found"}
    try:
        if req.action == 'file':
            os.startfile(req.path)
        elif req.action == 'folder':
            subprocess.Popen(['explorer', f'/select,{req.path}'])
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
