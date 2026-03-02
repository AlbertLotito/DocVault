import configparser
import os
import uuid
from qdrant_client import AsyncQdrantClient, models

async def check_qdrant_health():
    """
    Performs an async health check on the Qdrant database by connecting, 
    writing, reading, and then deleting a test point.
    """
    try:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
        host = cfg.get('qdrant', 'host', fallback='localhost')
        port = cfg.getint('qdrant', 'port', fallback=6333)
        collection = "_healthcheck"
        vector_size = 4 # Simple vector for a simple test

        client = AsyncQdrantClient(host=host, port=port, timeout=30.0)

        # 1. Check connection and collection
        await client.recreate_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.DOT)
        )
        
        # 2. Write an element
        test_id = str(uuid.uuid4())
        await client.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(id=test_id, vector=[0.1, 0.2, 0.3, 0.4], payload={"test": "ok"})
            ],
            wait=True
        )

        # 3. Read the element back
        retrieved_result = await client.retrieve(collection_name=collection, ids=[test_id])
        retrieved = retrieved_result
        if not retrieved or retrieved[0].payload.get("test") != "ok":
            raise Exception("Failed to retrieve the correct test point.")

        # 4. Clean up
        await client.delete_collection(collection_name=collection)

        return {
            "status": "ok",
            "message": f"Successfully connected to Qdrant at {host}:{port}. Performed write, read, and delete operations."
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Qdrant health check failed: {e}"
        }
