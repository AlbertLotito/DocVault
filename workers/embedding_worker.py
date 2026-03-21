import os
import socket
import subprocess
import time
from core import manager, logger
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


def _try_restart_qdrant(last_restart_at: float) -> float:
    """
    Attempt to restart the Qdrant Docker container.
    Respects a cooldown to avoid restart storms.
    Returns the updated last_restart_at timestamp.
    """
    from core.settings import settings

    enabled = str(settings.get('qdrant:auto_restart') or 'true').lower() not in ('0', 'false', 'no', 'off')
    if not enabled:
        return last_restart_at

    cooldown_secs = int(settings.get('qdrant:restart_cooldown_mins') or 10) * 60
    if time.time() - last_restart_at < cooldown_secs:
        logger.warn("Qdrant auto-restart skipped — still within cooldown window")
        return last_restart_at

    container = settings.get('qdrant:container_name') or 'docvault-qdrant-1'
    logger.error(f"Qdrant unreachable — attempting 'docker restart {container}'")

    try:
        result = subprocess.run(
            ['docker', 'restart', container],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.error(f"Qdrant container '{container}' restarted. Waiting 15s for it to come up...")
            time.sleep(15)
            try:
                from core.alerts import send_alert
                send_alert(
                    title="Qdrant auto-restarted",
                    message=f"DocVault automatically restarted the Qdrant container '{container}' after repeated connection failures.",
                    level='warning',
                    source='embedding_worker',
                )
            except Exception:
                pass
            return time.time()
        else:
            logger.error(f"docker restart failed (exit {result.returncode}): {result.stderr.strip()}")
    except FileNotFoundError:
        logger.warn("docker not found on PATH — cannot auto-restart Qdrant")
    except Exception as e:
        logger.error(f"Qdrant auto-restart error: {e}")

    return last_restart_at


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

    for i, chunk_text in enumerate(chunks):
        prog_pct = int((i / len(chunks)) * 100)
        manager.update_task_progress(db_path, file_hash, f"Embedding chunk {i+1}/{len(chunks)}...", prog_pct)

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
            # Qdrant connection failure — reset task to EXTRACTED so it is
            # retried automatically when Qdrant comes back. Never mark ERROR
            # for a transient infrastructure failure.
            logger.error(f"Qdrant upsert failed on chunk {i}: {e}. Resetting task to EXTRACTED.")
            try:
                manager.update_task_status(db_path, file_hash, status='EXTRACTED')
            except Exception as e2:
                logger.error(
                    f"Embedding worker: could not reset task {file_hash[:8]} to EXTRACTED — "
                    f"task may be stuck in EMBEDDING: {e2}"
                )
            raise  # re-raise so outer loop detects connection loss and resets vs

    manager.update_task_status(db_path, file_hash, status='COMPLETED')
    logger.info(f"  Embedded {len(chunks)} chunk(s).")


def run(db_path, shutdown_event=None, worker_id=None):
    if worker_id is None:
        worker_id = f"embed-{socket.gethostname()}-{os.getpid()}"
    logger.info(f"Embedding worker starting. ID: {worker_id}")

    vs = None
    connect_failures = 0
    last_restart_at = 0.0

    while True:
        if vs is None:
            try:
                logger.info("Embedding worker connecting to vector store...")
                vs = _load_vector_store()
                logger.info("Embedding worker connected to vector store.")
                connect_failures = 0
            except Exception as e:
                connect_failures += 1
                logger.error(f"Embedding worker could not connect to Qdrant (attempt {connect_failures}). Retrying in 10s... Error: {e}")

                from core.settings import settings
                threshold = int(settings.get('qdrant:restart_after_failures') or 3)
                if connect_failures >= threshold:
                    last_restart_at = _try_restart_qdrant(last_restart_at)
                    connect_failures = 0  # reset so we give it fresh attempts after restart

                time.sleep(10)
                continue

        skip, reason = should_pause_or_throttle()
        if skip:
            logger.info(f"Embedding worker {reason}. Sleeping...")
            try:
                interruptible_sleep(db_path, 10)
            except Exception:
                time.sleep(10)
            continue

        extra = get_throttle_sleep(reason)
        if extra:
            time.sleep(extra)

        try:
            task = manager.claim_extracted_task(db_path, worker_id)
        except Exception as e:
            logger.warn(f"Embedding worker DB contention, retrying in 5s: {e}")
            time.sleep(5)
            continue

        if task:
            try:
                process_task(db_path, task, vs)
            except Exception as e:
                # process_task re-raises on Qdrant failure after resetting to EXTRACTED.
                # Reset vs so next iteration re-connects.
                logger.error(f"Qdrant connection lost. Will reconnect in 10s. Error: {e}")
                vs = None
        else:
            try:
                interruptible_sleep(db_path, 10)
            except Exception:
                time.sleep(10)
