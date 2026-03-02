from qdrant_client import QdrantClient
from qdrant_client.http import models
import uuid


class VectorStore:
    def __init__(self, host: str, port: int, collection: str, vector_size: int = 768):
        self.client = QdrantClient(host=host, port=port)
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
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS,
                                   f"{file_hash}:{chunk_index}"))
        self.client.upsert(
            collection_name=self.collection,
            points=[models.PointStruct(
                id=point_id,
                vector=vector,
                payload={**payload, 'file_hash': file_hash,
                         'chunk_index': chunk_index}
            )]
        )

    def search(self, query_vector: list[float],
               top_k: int = 5, score_threshold: float = None,
               hash_filter: list[str] = None) -> list[dict]:
        if hash_filter is not None and len(hash_filter) == 0:
            return []  # Constraints matched no files
        kwargs = dict(collection_name=self.collection, query=query_vector, limit=top_k)
        if score_threshold is not None:
            kwargs['score_threshold'] = score_threshold
        if hash_filter is not None:
            kwargs['query_filter'] = models.Filter(must=[
                models.FieldCondition(
                    key='file_hash',
                    match=models.MatchAny(any=hash_filter),
                )
            ])
        response = self.client.query_points(**kwargs)
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
