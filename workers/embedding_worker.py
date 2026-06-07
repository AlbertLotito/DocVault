import os
import socket
import threading
import time
from core import manager, logger
from core.settings import settings
from embeddings import chunker, embedder
from embeddings.vector_store import VectorStore
from workers.utils import interruptible_sleep, paused_sleep, should_pause_or_throttle, get_throttle_sleep

_index_lock = threading.Lock()
_MIN_INDEX_ROWS = 1_000
_batches_since_index = 0


def _rebuild_index(vs: VectorStore) -> None:
    """Rebuild (or build) the IVF-PQ index — runs in a daemon thread.

    Skipped if another rebuild is already in progress.  LanceDB's optimize()
    crashes under load on large tables (native stack overrun), so we use a full
    create_vector_index() on a periodic schedule instead.
    """
    if not _index_lock.acquire(blocking=False):
        return
    try:
        n = vs.count()
        if n < _MIN_INDEX_ROWS:
            return
        logger.info(f"[embed] Rebuilding vector index on {n:,} rows...")
        vs.create_vector_index()
        logger.info("[embed] Vector index rebuild complete.")
    except Exception as e:
        logger.warn(f"[embed] Vector index rebuild failed: {e}")
    finally:
        _index_lock.release()


def _load_vector_store():
    return VectorStore()


def process_task(db_path, task, vs):
    file_hash = task['file_hash']
    file_path = task['file_path']
    filename  = os.path.basename(file_path)
    text = task.get('extracted_text') or ''

    logger.info(f"Embedding: {filename}")
    manager.update_task_progress(db_path, file_hash, f"Chunking {filename}...", 0)

    chunks = chunker.chunk(text)
    if not chunks:
        manager.update_task_status(db_path, file_hash, 'COMPLETED')
        logger.info("  No text to embed. Task marked as complete.")
        return

    # Embed all chunks in a single batch call (one HTTP round-trip to Ollama).
    # Prepend filename so the vector captures file identity as well as content;
    # payload keeps the clean text for display and LLM context.
    manager.update_task_progress(db_path, file_hash, f"Embedding {len(chunks)} chunk(s)...", 10)
    inputs  = [f"{filename}\n{chunk_text}" for chunk_text in chunks]
    vectors = embedder.embed_batch(inputs)

    batch = [
        {
            'chunk_index': i,
            'vector':      vector,
            'payload': {
                'file_path':   file_path,
                'chunk_text':  chunk_text,
                'chunk_index': i,
            },
        }
        for i, (chunk_text, vector) in enumerate(zip(chunks, vectors))
        if vector is not None
    ]

    if batch:
        manager.update_task_progress(db_path, file_hash, f"Storing {len(batch)} vectors...", 80)
        try:
            vs.upsert_batch(file_hash, batch)
        except Exception as e:
            # Vector store failure — reset task to EXTRACTED so it is retried.
            # Never mark ERROR for a transient infrastructure failure.
            logger.error(f"Vector upsert failed: {e}. Resetting task to EXTRACTED.")
            try:
                manager.update_task_status(db_path, file_hash, status='EXTRACTED')
            except Exception as e2:
                logger.error(
                    f"Embedding worker: could not reset task {file_hash[:8]} to EXTRACTED — "
                    f"task may be stuck in EMBEDDING: {e2}"
                )
            raise  # re-raise so outer loop detects connection loss and resets vs

    manager.update_task_status(db_path, file_hash, status='COMPLETED')
    logger.info(f"  Embedded {len(batch)} chunk(s).")


def process_task_batch(db_path, tasks, vs):
    """Process a list of EMBEDDING tasks: chunk all → one embed call → upsert per doc."""
    task_by_hash = {t['file_hash']: t for t in tasks}
    # Phase 1: chunk all documents
    # each entry: (task, chunk_index, chunk_text, embed_input_string)
    all_entries = []
    empty_hashes = []

    for task in tasks:
        file_hash = task['file_hash']
        file_path = task['file_path']
        filename  = os.path.basename(file_path)
        text = task.get('extracted_text') or ''
        chunks = chunker.chunk(text)
        if not chunks:
            empty_hashes.append(file_hash)
            continue
        for i, chunk_text in enumerate(chunks):
            all_entries.append((task, i, chunk_text, f"{filename}\n{chunk_text}"))

    for fh in empty_hashes:
        manager.update_task_status(db_path, fh, 'COMPLETED')

    if not all_entries:
        return

    n_docs   = len({e[0]['file_hash'] for e in all_entries})
    n_chunks = len(all_entries)
    logger.info(f"Embedding batch: {n_docs} doc(s), {n_chunks} chunk(s)")

    batch_start = time.time()
    # Phase 2: embed in sub-batches to avoid overwhelming Ollama
    max_chunks = int(settings.get('workers:embed_chunk_limit') or 200)
    inputs = [e[3] for e in all_entries]
    vectors = []
    for i in range(0, len(inputs), max_chunks):
        skip, _ = should_pause_or_throttle()
        if skip:
            logger.info("Embedding worker paused mid-batch — resetting tasks to EXTRACTED.")
            for fh in task_by_hash:
                try:
                    manager.update_task_status(db_path, fh, status='EXTRACTED')
                except Exception:
                    pass
            return
        vectors.extend(embedder.embed_batch(inputs[i:i + max_chunks]))

    # Phase 3: group vectors back by document
    doc_batches: dict = {}
    for (task, chunk_index, chunk_text, _), vector in zip(all_entries, vectors):
        fh = task['file_hash']
        if fh not in doc_batches:
            doc_batches[fh] = []
        if vector is not None:
            doc_batches[fh].append({
                'chunk_index': chunk_index,
                'vector':      vector,
                'payload': {
                    'file_path':   task['file_path'],
                    'chunk_text':  chunk_text,
                    'chunk_index': chunk_index,
                },
            })

    # Phase 4: upsert all docs in a single LanceDB merge_insert call, then mark complete.
    # If upsert fails, reset ALL non-empty docs to EXTRACTED before re-raising.
    non_empty = {fh: batch for fh, batch in doc_batches.items() if batch}
    try:
        if non_empty:
            vs.upsert_documents(non_empty)
    except Exception as e:
        logger.error(f"Vector upsert failed: {e}. Resetting batch to EXTRACTED.")
        for fh in doc_batches:
            try:
                manager.update_task_status(db_path, fh, status='EXTRACTED')
            except Exception as e2:
                logger.error(f"Could not reset {fh[:8]} to EXTRACTED: {e2}")
        raise

    for fh in doc_batches:
        manager.update_task_status(db_path, fh, status='COMPLETED')
    logger.info(f"  Batch complete: {len(doc_batches)} doc(s).")
    elapsed_per_doc = (time.time() - batch_start) / max(len(doc_batches), 1)
    for fh in doc_batches:
        _record_timing(task_by_hash[fh], 'embedding', elapsed_per_doc)


def _record_timing(task: dict, extractor_name: str, elapsed: float):
    """Best-effort write to logs.db task_timings."""
    try:
        from datetime import datetime, timezone
        from core.manager import get_logs_db_path, _connect
        with _connect(get_logs_db_path()) as conn:
            conn.execute(
                """INSERT INTO task_timings
                   (file_hash, vault_id, extractor, file_size, elapsed_secs, completed_at)
                   VALUES (?,?,?,?,?,?)""",
                (task['file_hash'], task.get('vault_id'), extractor_name,
                 task.get('file_size'), elapsed,
                 datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
    except Exception:
        pass


def run(db_path, shutdown_event=None, worker_id=None):
    if worker_id is None:
        worker_id = f"embed-{socket.gethostname()}-{os.getpid()}"
    logger.info(f"Embedding worker starting. ID: {worker_id}")

    try:
        vs = _load_vector_store()
        logger.info("Embedding worker: vector store ready.")
    except Exception as e:
        logger.error(f"Embedding worker: could not open vector store: {e}")
        return

    while True:
        skip, reason = should_pause_or_throttle()
        if skip:
            logger.info(f"Embedding worker {reason}. Sleeping...")
            if reason == 'paused':
                paused_sleep()
            else:
                try:
                    interruptible_sleep(db_path, 10)
                except Exception:
                    time.sleep(10)
            continue

        extra = get_throttle_sleep(reason)
        if extra:
            time.sleep(extra)

        try:
            batch_size = int(settings.get('workers:embed_batch_size') or 8)
            tasks = manager.claim_extracted_tasks(db_path, worker_id, limit=batch_size)
        except Exception as e:
            logger.warn(f"Embedding worker DB contention, retrying in 5s: {e}")
            time.sleep(5)
            continue

        if tasks:
            try:
                process_task_batch(db_path, tasks, vs)
            except Exception as e:
                logger.error(f"Embedding batch failed: {e}. Retrying in 10s.")
                continue

            global _batches_since_index
            _batches_since_index += 1
            rebuild_every = int(settings.get('workers:index_rebuild_every') or 100)
            if _batches_since_index >= rebuild_every:
                _batches_since_index = 0
                threading.Thread(target=_rebuild_index, args=(vs,), daemon=True).start()
        else:
            try:
                interruptible_sleep(db_path, 10)
            except Exception:
                time.sleep(10)
