import asyncio
from embeddings import embedder
from embeddings.vector_store import VectorStore


def _vs() -> VectorStore:
    return VectorStore()


def _threshold() -> float:
    from core.settings import settings
    return float(settings.get('embeddings:score_threshold') or 0.65)


def _missing_hashes(db_path: str) -> set[str]:
    try:
        from core import manager
        return manager.get_missing_hashes(manager.get_db_path(db_path))
    except Exception:
        return set()


def search(query: str, top_k: int = 5, hash_filter: list[str] = None,
           score_threshold: float = None, db_path: str = None) -> list[dict]:
    """Semantic search. Supports optional hash_filter for constraint pre-filtering."""
    vector = embedder.embed(query)
    if not vector:
        return []
    threshold = score_threshold if score_threshold is not None else _threshold()
    try:
        results = _vs().search(vector, top_k=top_k, score_threshold=threshold,
                               hash_filter=hash_filter)
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return []
    missing = _missing_hashes(db_path)
    return [r for r in results if r.get('file_hash') not in missing]


async def async_search(query: str, top_k: int = 5,
                       hash_filter: list[str] = None, db_path: str = None) -> list[dict]:
    """Async semantic search for FastAPI routes."""
    vector = await embedder.async_embed(query)
    if not vector:
        return []
    if hash_filter is not None and len(hash_filter) == 0:
        return []
    try:
        results = await asyncio.to_thread(
            _vs().search, vector,
            top_k, _threshold(), hash_filter,
        )
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return None  # None = failure; [] = success with no results
    missing = await asyncio.to_thread(_missing_hashes, db_path)
    return [r for r in results if r.get('file_hash') not in missing]
