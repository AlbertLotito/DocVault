import asyncio
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from search import fts, semantic, hybrid, spans
from search.query import detect_mode
from core import manager
from core.settings import settings
from core.monitor import notify_user_activity

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


def _limit(override: int = None) -> int:
    return override or int(settings.get('search:result_limit') or 20)


def _enrich_with_offsets(db: str, results: list[dict]) -> list[dict]:
    """Add chunk_offset, chunk_size, paragraph_num to each search result in-place."""
    if not results:
        return results

    file_hashes = list({r['file_hash'] for r in results if r.get('file_hash')})
    extracted_texts = manager.get_extracted_texts(db, file_hashes)
    chunk_size = int(settings.get('embeddings:chunk_size') or 600)

    for r in results:
        fh = r.get('file_hash', '')
        ext_text = extracted_texts.get(fh, '')
        chunk_index = int(r.get('chunk_index', 0))
        chunk_text = r.get('chunk_text', '')

        offset = spans.resolve_offset(chunk_index, chunk_text, ext_text) if ext_text else None
        para_num = spans.paragraph_number(ext_text, offset) if (ext_text and offset is not None) else None

        r['chunk_offset'] = offset
        r['chunk_size'] = chunk_size
        r['paragraph_num'] = para_num

    return results


@router.get("/search")
async def search(q: str = Query(..., min_length=1),
                 mode: str = Query('hybrid', pattern='^(fts|semantic|hybrid)$'),
                 limit: int = None,
                 file_type: str = None,
                 date_from: str = None,
                 date_to: str = None,
                 vault_ids: str = None):
    notify_user_activity()
    n = _limit(limit)
    db = get_db()
    vault_id_list = [v.strip() for v in vault_ids.split(',') if v.strip()] if vault_ids else None

    # Regex and wildcard queries can't be embedded — skip vector search
    query_mode, _ = detect_mode(q)
    vector_unsupported = query_mode in ('regex', 'wildcard')

    if mode == 'fts' or vector_unsupported:
        results = fts.search(db, q, n,
                             file_type=file_type, date_from=date_from, date_to=date_to,
                             vault_ids=vault_id_list)
        _enrich_with_offsets(db, results)
        if vector_unsupported and mode != 'fts':
            return JSONResponse(content={
                'results': results,
                'degraded': True,
                'degraded_reason': (
                    'Regex and wildcard queries use full-text search only — '
                    'vector search is not available for this query shape'
                ),
            })
        return JSONResponse(content={'results': results, 'degraded': False})

    # For semantic/hybrid: resolve SQLite hash filter once
    hash_filter = manager.get_filtered_hashes(
        db, file_type=file_type, date_from=date_from, date_to=date_to,
        vault_ids=vault_id_list
    )

    if mode == 'semantic':
        results = await semantic.async_search(q, top_k=n, hash_filter=hash_filter)
        manager.substitute_vault_paths(db, results, vault_id_list)
        # semantic already returns [] gracefully when Qdrant is down
        _enrich_with_offsets(db, results)
        return JSONResponse(content={'results': results, 'degraded': False})

    results, semantic_offline = await hybrid.async_search(
        db_path=db, query=q, top_k=n,
        file_type=file_type, date_from=date_from, date_to=date_to,
        hash_filter=hash_filter, vault_ids=vault_id_list
    )
    manager.substitute_vault_paths(db, results, vault_id_list)
    _enrich_with_offsets(db, results)
    return JSONResponse(content={
        'results': results,
        'degraded': semantic_offline,
        'degraded_reason': 'Semantic search unavailable — showing full-text results only' if semantic_offline else '',
    })


@router.get("/search/filename")
def search_filename(q: str = Query(..., min_length=1),
                    limit: int = None,
                    file_type: str = None,
                    date_from: str = None,
                    date_to: str = None,
                    vault_ids: str = None):
    """Search for files by name/path using multi-token substring matching."""
    notify_user_activity()
    vault_id_list = [v.strip() for v in vault_ids.split(',') if v.strip()] if vault_ids else None
    return manager.filename_search(
        get_db(), q, _limit(limit),
        file_type=file_type, date_from=date_from, date_to=date_to,
        vault_ids=vault_id_list,
    )
