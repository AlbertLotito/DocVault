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
                       hash_filter: set[str] | None = None) -> tuple[list[dict], bool]:
    """
    Perform a hybrid search: FTS and Semantic in parallel, then RRF merge.
    Returns (results, qdrant_offline) where qdrant_offline=True means Qdrant was
    unreachable and results are FTS-only.
    """
    fts_task      = asyncio.to_thread(fts.search, db_path, query, top_k * 2,
                                      file_type, date_from, date_to)
    semantic_task = semantic.async_search(query, top_k=top_k * 2, hash_filter=hash_filter)

    fts_r, sem_r = await asyncio.gather(fts_task, semantic_task, return_exceptions=True)

    # Handle exceptions from either task (semantic already returns [] on Qdrant failure,
    # but guard here too in case of unexpected errors)
    if isinstance(fts_r, Exception):
        fts_r = []
    if isinstance(sem_r, Exception):
        sem_r = []

    qdrant_offline = len(sem_r) == 0  # heuristic: if no semantic results at all
    merged = merge(fts_r, sem_r)
    return merged[:top_k], qdrant_offline
