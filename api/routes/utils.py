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


@router.get("/utils/health")
def system_health():
    """
    Diagnose common task-queue problems.
    Returns a list of issues with severity and available fix actions.
    """
    from api.main import DB_PATH
    import sqlite3

    issues = []

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    try:
        # Count by status
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks GROUP BY status"
        ).fetchall()
        counts = {r['status']: r['n'] for r in rows}

        # 1. Stuck PROCESSING tasks (worker process died, task will never complete)
        stuck = counts.get('PROCESSING', 0)
        if stuck:
            issues.append({
                'id': 'stuck_processing',
                'severity': 'warning',
                'title': f'{stuck} stuck task{"s" if stuck != 1 else ""}',
                'detail': 'Claimed by worker processes that no longer exist. Will never complete without a reset.',
                'action': 'reset_stuck',
                'action_label': f'Reset {stuck} to PENDING',
                'count': stuck,
            })

        # 2. Qdrant embedding failures (safe to retry — Qdrant may have been down)
        qdrant_errs = conn.execute(
            """SELECT COUNT(*) as n FROM tasks
               WHERE status='ERROR'
               AND (error_log LIKE '%upsert%' OR error_log LIKE '%Qdrant%'
                    OR error_log LIKE '%timed out%')"""
        ).fetchone()['n']
        if qdrant_errs:
            issues.append({
                'id': 'qdrant_errors',
                'severity': 'warning',
                'title': f'{qdrant_errs} Qdrant embedding failure{"s" if qdrant_errs != 1 else ""}',
                'detail': 'Extracted text is intact. Failed only at the embedding/upload step. Safe to retry.',
                'action': 'retry_embed_errors',
                'action_label': f'Retry {qdrant_errs} (reset to EXTRACTED)',
                'count': qdrant_errs,
            })

        # 3. Encoding errors (need a code fix — don't auto-retry)
        enc_errs = conn.execute(
            """SELECT COUNT(*) as n FROM tasks
               WHERE status='ERROR'
               AND (error_log LIKE '%charmap%' OR error_log LIKE '%codec%encode%')"""
        ).fetchone()['n']
        if enc_errs:
            issues.append({
                'id': 'encoding_errors',
                'severity': 'info',
                'title': f'{enc_errs} Unicode encoding error{"s" if enc_errs != 1 else ""}',
                'detail': 'OCR extractor failed encoding Unicode characters. Requires a code fix before retrying.',
                'action': 'retry_extract_errors',
                'action_label': f'Retry {enc_errs} anyway (reset to PENDING)',
                'count': enc_errs,
            })

        # 4. Other errors (empty files, genuine failures — info only)
        other_errs = counts.get('ERROR', 0) - qdrant_errs - enc_errs
        if other_errs > 0:
            issues.append({
                'id': 'other_errors',
                'severity': 'info',
                'title': f'{other_errs} other extraction error{"s" if other_errs != 1 else ""}',
                'detail': 'Empty files, unsupported formats, or missing external services. Inspect the Vault Log for details.',
                'action': None,
                'action_label': None,
                'count': other_errs,
            })
    finally:
        conn.close()

    # 5. Qdrant connectivity
    qdrant_ok = False
    qdrant_points = 0
    qdrant_detail = ''
    try:
        from qdrant_client import QdrantClient
        host = settings.get('qdrant:host')
        port = int(settings.get('qdrant:port'))
        client = QdrantClient(host=host, port=port)
        col = client.get_collection('docvault')
        qdrant_ok = True
        qdrant_points = col.points_count
    except Exception as e:
        qdrant_detail = str(e)
        issues.append({
            'id': 'qdrant_down',
            'severity': 'error',
            'title': 'Qdrant unreachable',
            'detail': f'Embedding worker cannot store vectors: {qdrant_detail}',
            'action': None,
            'action_label': None,
            'count': 0,
        })

    return {
        'issues': issues,
        'counts': counts,
        'qdrant': {'ok': qdrant_ok, 'points': qdrant_points},
    }


def _db_write(db_path, sql, params=()):
    """Execute a single write against docvault.db with WAL mode and a generous timeout."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@router.post("/utils/reset_stuck")
def reset_stuck():
    """Reset all PROCESSING tasks to PENDING (orphaned by dead worker processes)."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        "UPDATE tasks SET status='PENDING', worker_id=NULL, last_update=datetime('now') "
        "WHERE status='PROCESSING'"
    )
    return {'ok': True, 'reset': n}


@router.post("/utils/retry_embed_errors")
def retry_embed_errors():
    """Reset Qdrant-timeout ERROR tasks to EXTRACTED so the embedding worker retries."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        """UPDATE tasks SET status='EXTRACTED', worker_id=NULL, last_update=datetime('now')
           WHERE status='ERROR'
           AND (error_log LIKE '%upsert%' OR error_log LIKE '%Qdrant%'
                OR error_log LIKE '%timed out%')"""
    )
    return {'ok': True, 'reset': n}


@router.post("/utils/retry_extract_errors")
def retry_extract_errors():
    """Reset encoding/OCR ERROR tasks to PENDING so the extraction worker retries."""
    from api.main import DB_PATH
    n = _db_write(DB_PATH,
        """UPDATE tasks SET status='PENDING', worker_id=NULL, last_update=datetime('now')
           WHERE status='ERROR'
           AND (error_log LIKE '%charmap%' OR error_log LIKE '%codec%encode%')"""
    )
    return {'ok': True, 'reset': n}


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
