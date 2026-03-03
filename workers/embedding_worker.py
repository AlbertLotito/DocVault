import os
import socket
import time
from core import manager
from embeddings import chunker, embedder
from embeddings.vector_store import VectorStore
from workers.utils import interruptible_sleep, should_pause_or_throttle, get_throttle_sleep


def _load_vector_store(vault_id: str | None = None):
    from core.settings import SettingsResolver
    r = SettingsResolver(vault_id=vault_id)
    return VectorStore(
        host=r.get('qdrant:host'),
        port=int(r.get('qdrant:port')),
        collection='docvault',
    )


def process_task(db_path, task, vs):
    file_hash = task['file_hash']
    file_path = task['file_path']
    text = task.get('extracted_text') or ''

    print(f"Embedding: {os.path.basename(file_path)}")

    chunks = chunker.chunk(text)
    if not chunks:
        manager.update_task_status(db_path, file_hash, 'COMPLETED')
        print("  No text to embed. Task marked as complete.")
        return

    filename = os.path.basename(file_path)
    for i, chunk_text in enumerate(chunks):
        # Prepend the filename so vector captures file identity as well as content.
        # Payload keeps the clean text for display and LLM context.
        vector = embedder.embed(f"{filename}\n{chunk_text}")
        if not vector:
            # embedder already prints the error from ollama
            continue

        try:
            vs.upsert(
                file_hash=file_hash,
                chunk_index=i,
                vector=vector,
                payload={
                    'file_path': file_path,
                    'chunk_text': chunk_text,
                    'chunk_index': i,
                }
            )
        except Exception as e:
            error_msg = f"Failed to upsert chunk {i} to Qdrant: {e}"
            print(f"  [ERROR] {error_msg}")
            manager.complete_extraction(db_path, file_hash, status='ERROR', error=error_msg)
            return

    manager.update_task_status(db_path, file_hash, status='COMPLETED')
    print(f"  Embedded {len(chunks)} chunk(s).")


def run(db_path, shutdown_event=None, worker_id=None): # shutdown_event is ignored
    if worker_id is None:
        worker_id = f"embed-{socket.gethostname()}-{os.getpid()}"
    print(f"Embedding worker starting. ID: {worker_id}")

    vs = None
    while True: # This is a daemon thread, it will be terminated on main exit
        if vs is None:
            try:
                print("Embedding worker connecting to vector store...")
                vs = _load_vector_store()
                print("Embedding worker connected to vector store.")
            except Exception as e:
                print(f"[ERROR] Embedding worker could not connect to Qdrant. Retrying in 10s... Error: {e}")
                time.sleep(10)
                continue

        skip, reason = should_pause_or_throttle()
        if skip:
            print(f"Embedding worker {reason}. Sleeping...")
            interruptible_sleep(db_path, 10)
            continue

        extra = get_throttle_sleep(reason)
        if extra:
            time.sleep(extra)

        task = manager.claim_extracted_task(db_path, worker_id)
        if task:
            try:
                process_task(db_path, task, vs)
            except Exception as e:
                # If we get an exception here, it might be a connection error.
                # Reset vs so we try to reconnect on the next loop.
                if "connection" in str(e).lower():
                    print(f"[ERROR] Qdrant connection lost. Will attempt to reconnect. Error: {e}")
                    vs = None 
                else:
                    print(f"[ERROR] Unhandled exception in embedding worker for task {task.get('file_hash')}: {e}")
                manager.update_task_status(db_path, task['file_hash'], status='ERROR')

        else:
            print("No extracted tasks. Sleeping...")
            interruptible_sleep(db_path, 10)
