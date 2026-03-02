import asyncio
from fastapi import APIRouter, Query
from search import fts, semantic, hybrid
from core import manager
from core.settings import settings

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
    n = _limit(limit)
    db = get_db()

    # For semantic/hybrid: resolve SQLite hash filter once
    hash_filter = manager.get_filtered_hashes(
        db, file_type=file_type, date_from=date_from, date_to=date_to
    )

    if mode == 'fts':
        return fts.search(db, q, n,
                          file_type=file_type, date_from=date_from, date_to=date_to)

    if mode == 'semantic':
        return await semantic.async_search(q, top_k=n, hash_filter=hash_filter)

    fts_task      = asyncio.to_thread(fts.search, db, q, n,
                                      file_type, date_from, date_to)
    semantic_task = semantic.async_search(q, top_k=n, hash_filter=hash_filter)
    fts_r, sem_r  = await asyncio.gather(fts_task, semantic_task)
    return hybrid.merge(fts_r, sem_r)


@router.get("/search/filename")
def search_filename(q: str = Query(..., min_length=1),
                    limit: int = None,
                    file_type: str = None,
                    date_from: str = None,
                    date_to: str = None):
    """Search for files by name/path using multi-token substring matching."""
    return manager.filename_search(
        get_db(), q, _limit(limit),
        file_type=file_type, date_from=date_from, date_to=date_to,
    )
