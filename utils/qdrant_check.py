from embeddings.vector_store import VectorStore


async def check_qdrant_health():
    """Health check for the vector store (kept for backward compatibility)."""
    try:
        count = VectorStore().count()
        return {'status': 'ok', 'vectors': count}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}
