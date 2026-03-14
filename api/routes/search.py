import asyncio
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from search import fts, semantic, hybrid
from core import manager
from core.settings import settings
from core.monitor import notify_user_activity

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


def _limit(override: int = None) -> int:
    return override or int(settings.get('search:result_limit') or 20)


@router.get("/search")
async def search(q: str = Query(..., min_length=1),
                 mode: str = Query('hybrid', pattern='^(fts|semantic|hybrid)$'),
                 limit: int = None,
                 file_type: str = None,
                 date_from: str = None,
                 date_to: str = None):
    notify_user_activity()
    n = _limit(limit)
    db = get_db()

    # For semantic/hybrid: resolve SQLite hash filter once
    hash_filter = manager.get_filtered_hashes(
        db, file_type=file_type, date_from=date_from, date_to=date_to
    )

    if mode == 'fts':
        results = fts.search(db, q, n,
                             file_type=file_type, date_from=date_from, date_to=date_to)
        return JSONResponse(content={'results': results, 'degraded': False})

    if mode == 'semantic':
        results = await semantic.async_search(q, top_k=n, hash_filter=hash_filter)
        # semantic already returns [] gracefully when Qdrant is down
        return JSONResponse(content={'results': results, 'degraded': False})

    results, qdrant_offline = await hybrid.async_search(
        db_path=db, query=q, top_k=n,
        file_type=file_type, date_from=date_from, date_to=date_to,
        hash_filter=hash_filter
    )
    if qdrant_offline:
        # Qdrant offline — fall back to FTS-only and tell the UI
        fts_results = fts.search(db, q, n,
                                 file_type=file_type, date_from=date_from, date_to=date_to)
        return JSONResponse(content={
            'results': fts_results,
            'degraded': True,
            'degraded_reason': 'Qdrant unavailable — showing full-text results only',
        })
    return JSONResponse(content={'results': results, 'degraded': False})


@router.get("/search/filename")
def search_filename(q: str = Query(..., min_length=1),
                    limit: int = None,
                    file_type: str = None,
                    date_from: str = None,
                    date_to: str = None):
    """Search for files by name/path using multi-token substring matching."""
    notify_user_activity()
    return manager.filename_search(
        get_db(), q, _limit(limit),
        file_type=file_type, date_from=date_from, date_to=date_to,
    )
