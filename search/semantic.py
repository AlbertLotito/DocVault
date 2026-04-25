import asyncio
from embeddings import embedder
from embeddings.vector_store import VectorStore


def _vs() -> VectorStore:
    return VectorStore()


def _threshold() -> float:
    from core.settings import settings
    return float(settings.get('embeddings:score_threshold') or 0.65)


def search(query: str, top_k: int = 5, hash_filter: list[str] = None,
           score_threshold: float = None) -> list[dict]:
    """Semantic search. Supports optional hash_filter for constraint pre-filtering."""
    vector = embedder.embed(query)
    if not vector:
        return []
    threshold = score_threshold if score_threshold is not None else _threshold()
    try:
        return _vs().search(vector, top_k=top_k, score_threshold=threshold,
                            hash_filter=hash_filter)
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return []


async def async_search(query: str, top_k: int = 5,
                       hash_filter: list[str] = None) -> list[dict]:
    """Async semantic search for FastAPI routes."""
    vector = await embedder.async_embed(query)
    if not vector:
        return []
    if hash_filter is not None and len(hash_filter) == 0:
        return []
    try:
        return await asyncio.to_thread(
            _vs().search, vector,
            top_k, _threshold(), hash_filter,
        )
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Vector search unavailable: {e}")
        return None  # None = failure; [] = success with no results
