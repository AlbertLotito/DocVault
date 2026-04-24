import sys
import os

def run_connection_test():
    """Tests LanceDB vector store connectivity."""
    print("--- Vector Store Connection Test ---")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    try:
        import core.manager
        from embeddings.vector_store import VectorStore
        vs = VectorStore()
        count = vs.count()
        print(f"[PASS] LanceDB connected. Vectors in store: {count:,}")
    except Exception as e:
        print(f"[FAIL] Could not connect to vector store: {e}")
        return
    print("--- Test passed ---")


if __name__ == "__main__":
    run_connection_test()
