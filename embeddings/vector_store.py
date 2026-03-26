from qdrant_client import QdrantClient
from qdrant_client.http import models
import uuid


class VectorStore:
    def __init__(self, host: str, port: int, collection: str, vector_size: int = 768):
        # Using 30s timeout to prevent ResponseHandlingException on heavy queries
        self.client = QdrantClient(host=host, port=port, timeout=30.0)
        self.collection = collection
        self._ensure_collection(vector_size)

    def _ensure_collection(self, vector_size):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                )
            )

    def upsert(self, file_hash: str, chunk_index: int,
               vector: list[float], payload: dict):
        self.upsert_batch(file_hash, [{'chunk_index': chunk_index,
                                       'vector': vector, 'payload': payload}])

    def upsert_batch(self, file_hash: str, chunks: list[dict]) -> None:
        """Batch-upsert multiple chunks in one Qdrant call.

        Each element of *chunks* must have keys:
            chunk_index: int
            vector:      list[float]
            payload:     dict  (file_path, chunk_text, chunk_index)
        """
        if not chunks:
            return
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS,
                                  f"{file_hash}:{c['chunk_index']}")),
                vector=c['vector'],
                payload={**c['payload'], 'file_hash': file_hash,
                         'chunk_index': c['chunk_index']},
            )
            for c in chunks
        ]
        self.client.upsert(collection_name=self.collection, points=points)

    def search(self, query_vector: list[float],
               top_k: int = 5, score_threshold: float = None,
               hash_filter: list[str] = None) -> list[dict]:
        if hash_filter is not None and len(hash_filter) == 0:
            return []  # Constraints matched no files
        
        qdrant_filter = None
        if hash_filter is not None:
            qdrant_filter = models.Filter(must=[
                models.FieldCondition(
                    key='file_hash',
                    match=models.MatchAny(any=hash_filter),
                )
            ])

        # Use query_points which is the recommended API in recent qdrant-client versions
        response = self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        return [{'score': r.score, **r.payload} for r in response.points]

    def update_path(self, file_hash: str, new_path: str):
        """Update the file_path payload on all vectors for a given file hash."""
        self.client.set_payload(
            collection_name=self.collection,
            payload={'file_path': new_path},
            points=models.Filter(
                must=[models.FieldCondition(
                    key='file_hash',
                    match=models.MatchValue(value=file_hash)
                )]
            ),
        )

    def delete_by_hash(self, file_hash: str):
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key='file_hash',
                        match=models.MatchValue(value=file_hash)
                    )]
                )
            )
        )
