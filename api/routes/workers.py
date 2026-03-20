import os
import time

from fastapi import APIRouter
from core import manager

router = APIRouter()

# Tracks wall-clock start time for in-flight EMBEDDING tasks.
# Key: file_hash  Value: time.time() when first seen in EMBEDDING state
_embed_start: dict[str, float] = {}


def get_db():
    from api.main import DB_PATH
    return DB_PATH


def _get_embedding_progress(db_path: str) -> dict | None:
    """
    Query the current EMBEDDING task and return progress info, or None.
    Maintains _embed_start for elapsed/ETA calculation.
    """
    import sqlite3
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT file_hash, file_path, progress_text, progress_pct "
                "FROM tasks WHERE status='EMBEDDING' LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except Exception:
        return None

    # Evict hashes no longer in EMBEDDING state
    current_hash = row['file_hash'] if row else None
    for h in list(_embed_start.keys()):
        if h != current_hash:
            del _embed_start[h]

    if not row:
        return None

    fhash = row['file_hash']
    if fhash not in _embed_start:
        _embed_start[fhash] = time.time()

    pct = int(row['progress_pct'] or 0)
    elapsed = time.time() - _embed_start[fhash]
    eta = (elapsed / pct * (100 - pct)) if pct >= 2 else None

    return {
        'filename':      os.path.basename(row['file_path']),
        'progress_text': row['progress_text'] or '',
        'pct':           pct,
        'elapsed_secs':  round(elapsed),
        'eta_secs':      round(eta) if eta is not None else None,
    }


@router.get("/workers/status")
def worker_status():
    from core.monitor import get_stall_state
    stalled, stall_mins = get_stall_state()
    return {
        'paused':             manager.get_pause_state(),
        'stalled':            stalled,
        'stall_minutes':      stall_mins,
        'embedding_progress': _get_embedding_progress(get_db()),
    }


@router.post("/workers/pause")
def pause():
    manager.set_pause_state(True)
    return {'paused': True}


@router.post("/workers/resume")
def resume():
    manager.set_pause_state(False)
    return {'paused': False}


@router.post("/workers/reset_stuck")
def reset_stuck():
    count = manager.reset_stuck_tasks(get_db())
    return {'reset': count}
