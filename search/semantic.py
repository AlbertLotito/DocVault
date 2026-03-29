from embeddings import embedder
from embeddings.vector_store import VectorStore
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models
from core.settings import settings


def _vs():
    return VectorStore(
        host=settings.get('qdrant:host'),
        port=int(settings.get('qdrant:port')),
        collection='docvault',
    )


def _threshold() -> float:
    return float(settings.get('embeddings:score_threshold') or 0.65)


def search(query: str, top_k: int = 5, hash_filter: list[str] = None,
           score_threshold: float = None) -> list[dict]:
    """Semantic search via Qdrant. Supports optional hash_filter for constraint pre-filtering."""
    vector = embedder.embed(query)
    if not vector:
        return []
    threshold = score_threshold if score_threshold is not None else _threshold()
    try:
        return _vs().search(vector, top_k=top_k, score_threshold=threshold,
                            hash_filter=hash_filter)
    except Exception as e:
        from core import logger
        logger.warn(f"[semantic] Qdrant unavailable: {e}")
        return []


async def async_search(query: str, top_k: int = 5,
                       hash_filter: list[str] = None) -> list[dict]:
    """Async semantic search via Qdrant. For use with FastAPI."""
    vector = await embedder.async_embed(query)
    if not vector:
        return []

    if hash_filter is not None and len(hash_filter) == 0:
        return []  # Constraints matched no files

    # Increase timeout to 30s to avoid ResponseHandlingException on large collections
    async_client = AsyncQdrantClient(
        host=settings.get('qdrant:host'),
        port=int(settings.get('qdrant:port')),
        timeout=30.0,
    )

    try:
        qdrant_filter = None
        if hash_filter is not None:
            qdrant_filter = qdrant_models.Filter(must=[
                qdrant_models.FieldCondition(
                    key='file_hash',
                    match=qdrant_models.MatchAny(any=hash_filter),
                )
            ])

        # Use query_points which is the recommended API in recent qdrant-client versions
        response = await async_client.query_points(
            collection_name='docvault',
            query=vector,
            limit=top_k,
            score_threshold=_threshold(),
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [{'score': r.score, **r.payload} for r in response.points]
    except Exception as e:
        # Qdrant unavailable — degrade gracefully rather than propagating a 500.
        # Return None (not []) so callers can distinguish failure from no results.
        from core import logger
        logger.warn(f"[semantic] Qdrant unavailable: {e}")
        return None
    finally:
        await async_client.close()
