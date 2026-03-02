import configparser
import os
import httpx
from qdrant_client import QdrantClient
import json

def run_connection_test():
    """
    Performs a series of connection tests to diagnose issues with Qdrant.
    """
    print("--- Starting Qdrant Connection Test ---")
    host = None
    port = None

    # --- Read Config ---
    try:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
        host = cfg.get('qdrant', 'host', fallback='localhost')
        port = cfg.getint('qdrant', 'port', fallback=6333)
        print(f"Read config: Targetting Qdrant at http://{host}:{port}")
    except Exception as e:
        print(f"\n[FAIL] Step 0: Failed to read configuration.")
        print(f"   Error: {e}")
        return

    # --- Test 1: Basic HTTP request with httpx (like curl) ---
    print("\n--- Test 1: Basic HTTP GET request with httpx ---")
    try:
        url = f"http://{host}:{port}/"
        with httpx.Client(timeout=5.0) as client:
            response = client.get(url)
            response.raise_for_status() 
        print(f"[PASS] Successfully received a {response.status_code} response from {url}.")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"[FAIL] Failed to perform a basic GET request.")
        print(f"   Error: {e}")
        return

    # --- Test 2: Read-only request with qdrant-client ---
    print("\n--- Test 2: Read-only info request with qdrant_client ---")
    try:
        client = QdrantClient(host=host, port=port, timeout=5)
        collections_result = client.get_collections()
        print(f"[PASS] Successfully connected with qdrant-client.")
        print(f"   Found {len(collections_result.collections)} collections.")
    except Exception as e:
        print(f"[FAIL] qdrant-client failed on a simple read-only operation.")
        print(f"   Error: {e}")
        return

    # --- Test 3: Manual WRITE request with httpx ---
    print("\n--- Test 3: Manual WRITE (PUT) request with httpx ---")
    collection_url = f"http://{host}:{port}/collections/_manual_test"
    try:
        with httpx.Client(timeout=5.0) as client:
            # Create collection
            put_payload = { "vectors": { "size": 4, "distance": "Dot" } }
            print(f"   Attempting PUT to {collection_url}...")
            response = client.put(collection_url, json=put_payload)
            response.raise_for_status()
            print(f"[PASS] Successfully performed a manual PUT request.")
            print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"[FAIL] Failed to perform a manual PUT request.")
        print(f"   Error: {e}")
        print("\n   >>> This is the critical failure point. It strongly suggests an")
        print("   >>> environmental issue (Qdrant config, network hardware) is")
        print("   >>> blocking HTTP PUT/POST requests from this machine's Python environment.")
        return
    finally:
        # Cleanup:
        try:
            with httpx.Client(timeout=5.0) as client:
                print(f"   Attempting DELETE to {collection_url} for cleanup...")
                client.delete(collection_url)
                print("   Cleanup successful.")
        except Exception as e:
            print(f"   [WARN] Cleanup failed. You may need to manually delete the '_manual_test' collection in Qdrant.")
            print(f"   Error: {e}")

    print("\n--- All tests passed! ---")


if __name__ == "__main__":
    run_connection_test()
