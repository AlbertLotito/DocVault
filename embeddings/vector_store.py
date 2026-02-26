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
               top_k: int = 5) -> list[dict]:
        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
        )
        return [{'score': r.score, **r.payload} for r in results]

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
