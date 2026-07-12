import asyncio
from search import fts, semantic


def merge(fts_results: list[dict], semantic_results: list[dict],
          fts_weight: float = None, sem_weight: float = None) -> list[dict]:
    """
    Reciprocal Rank Fusion (RRF) of FTS and semantic results at the chunk level.
    Returns deduplicated list sorted by combined RRF score descending.
    """
    from core.settings import settings
    if fts_weight is None:
        fts_weight = float(settings.get('search:fts_weight') or 0.4)
    if sem_weight is None:
        sem_weight = float(settings.get('search:sem_weight') or 0.6)

    rrf_scores = {}
    
    # Store data using (hash, index) as key
    fts_data  = {(r['file_hash'], int(r.get('chunk_index', 0))): r for r in fts_results}
    sem_data  = {(r['file_hash'], int(r.get('chunk_index', 0))): r for r in semantic_results}

    # K is the constant used in RRF (60 is standard)
    K = 60

    # Fusion for FTS
    for rank, r in enumerate(fts_results, start=1):
        key = (r['file_hash'], int(r.get('chunk_index', 0)))
        rrf_scores[key] = rrf_scores.get(key, 0) + fts_weight * (1.0 / (K + rank))

    # Fusion for Semantic
    for rank, r in enumerate(semantic_results, start=1):
        key = (r['file_hash'], int(r.get('chunk_index', 0)))
        rrf_scores[key] = rrf_scores.get(key, 0) + sem_weight * (1.0 / (K + rank))

    # Sort results by combined RRF score
    ranked_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)
    
    results = []
    for key in ranked_keys:
        # Prefer semantic payload if available (often richer)
        base = sem_data.get(key) or fts_data[key]
        results.append({
            **base,
            'score': sem_data[key]['score'] if key in sem_data else None,
            'combined_score': rrf_scores[key],
            'chunk_index': key[1],
        })
    return results


async def async_search(db_path: str, query: str, top_k: int = 10,
                       file_type: str = None, date_from: str = None, date_to: str = None,
                       hash_filter: set[str] | None = None,
                       vault_ids: list | None = None) -> tuple[list[dict], bool]:
    """
    Perform a hybrid search: FTS and Semantic in parallel, then RRF merge.
    Returns (results, semantic_offline) where semantic_offline=True means the
    vector store was unreachable/timed out and results are FTS-only.
    """
    from core.settings import settings
    sem_timeout = float(settings.get('search:semantic_timeout') or 20)

    fts_task      = asyncio.to_thread(fts.search, db_path, query, top_k * 2,
                                      file_type, date_from, date_to, vault_ids)
    # Wrap semantic search with a short timeout so a busy Ollama (all slots used by
    # vision/chat workers) falls back to FTS-only quickly rather than blocking until
    # the outer query/stream timeout fires.
    semantic_task = asyncio.wait_for(
        semantic.async_search(query, top_k=top_k * 2, hash_filter=hash_filter),
        timeout=sem_timeout,
    )

    fts_r, sem_r = await asyncio.gather(fts_task, semantic_task, return_exceptions=True)

    # Handle exceptions from either task (semantic already returns [] on vector
    # store failure, but guard here too in case of unexpected errors)
    if isinstance(fts_r, Exception):
        fts_r = []
    if isinstance(sem_r, Exception):
        from core import logger
        if isinstance(sem_r, asyncio.TimeoutError):
            logger.warn(f"[hybrid] Semantic search timed out after {sem_timeout}s — falling back to FTS-only")
        else:
            logger.warn(f"[hybrid] Semantic search failed ({sem_r}) — falling back to FTS-only")
        sem_r = None

    # None means semantic unavailable; [] means it worked but found nothing
    semantic_offline = sem_r is None
    merged = merge(fts_r, sem_r or [])
    return merged[:top_k], semantic_offline
