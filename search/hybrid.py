def merge(fts_results: list[dict], semantic_results: list[dict],
          fts_weight: float = None, sem_weight: float = None) -> list[dict]:
    """
    Reciprocal Rank Fusion of FTS and semantic results.
    Semantic data takes precedence when a file appears in both (preserves score).
    Returns deduplicated list sorted by combined RRF score descending.
    """
    from core.settings import settings
    if fts_weight is None:
        fts_weight = float(settings.get('search:fts_weight') or 0.4)
    if sem_weight is None:
        sem_weight = float(settings.get('search:sem_weight') or 0.6)

    rrf_scores = {}
    fts_data  = {r['file_hash']: r for r in fts_results}
    sem_data  = {r['file_hash']: r for r in semantic_results}

    for rank, r in enumerate(fts_results, start=1):
        h = r['file_hash']
        rrf_scores[h] = rrf_scores.get(h, 0) + fts_weight * (1.0 / (60 + rank))

    for rank, r in enumerate(semantic_results, start=1):
        h = r['file_hash']
        rrf_scores[h] = rrf_scores.get(h, 0) + sem_weight * (1.0 / (60 + rank))

    ranked = sorted(rrf_scores, key=lambda h: rrf_scores[h], reverse=True)
    results = []
    for h in ranked:
        # Prefer semantic payload (has cosine score); fall back to FTS
        base = sem_data.get(h) or fts_data[h]
        results.append({
            **base,
            'score': sem_data[h]['score'] if h in sem_data else None,
            'combined_score': rrf_scores[h],
        })
    return results
