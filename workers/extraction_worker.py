import os
import socket
import time
from core import manager, router
from extractors import image_extractor, unknown_extractor


def process_task(db_path, task):
    """
    Run all registered extractors for a task, store results.
    """
    file_hash = task['file_hash']
    file_path = task['file_path']
    file_type = task['file_type'] or ''

    print(f"Processing [{file_type}]: {os.path.basename(file_path)}")

    extractors = router.get_extractors(file_type)

    # Unknown file type — flag and move on
    if extractors == [unknown_extractor]:
        _, msg = unknown_extractor.extract(file_path)
        manager.complete_extraction(db_path, file_hash, status='UNKNOWN', error=msg)
        print(f"  Flagged as UNKNOWN: {file_type}")
        return

    errors = []
    combined_text = []
    combined_metadata = {}

    for extractor in extractors:
        result, err = extractor.extract(file_path)

        if err:
            errors.append(f"[{extractor.__name__}] {err}")
            continue

        # Image extractor returns a list of dicts — store metadata and collect descriptions
        if extractor is image_extractor and isinstance(result, list):
            for img_meta in result:
                description = img_meta.pop('description', None)
                manager.insert_extracted_image(db_path, file_hash, img_meta)
                if description:
                    label = f"[Image — page {img_meta.get('page_num', '?')}]"
                    combined_text.append(f"{label}\n{description}")

        # Text-returning extractors
        elif isinstance(result, str) and result:
            combined_text.append(result)

        # Metadata-returning extractors
        elif isinstance(result, dict):
            combined_metadata.update(result)

    final_text = "\n\n".join(combined_text) if combined_text else None
    has_content = bool(final_text or combined_metadata)
    final_status = 'ERROR' if (errors and not has_content) else 'EXTRACTED'

    manager.complete_extraction(
        db_path,
        file_hash,
        status=final_status,
        text=final_text,
        metadata=combined_metadata if combined_metadata else None,
        error=' | '.join(errors) if errors else None,
    )


from .utils import interruptible_sleep


def run(db_path, worker_id=None, shutdown_event=None): # shutdown_event is ignored but passed by run.py
    """Main loop — claim and process PENDING tasks until paused or empty."""
    if worker_id is None:
        worker_id = f"extract-{socket.gethostname()}-{os.getpid()}"
    print(f"Extraction worker starting. ID: {worker_id}")

    while True: # This is a daemon thread, it will be terminated on main exit
        if manager.get_pause_state():
            print("Extraction worker paused. Sleeping...")
            interruptible_sleep(db_path, 10)
            continue

        task = manager.claim_pending_task(db_path, worker_id)
        if task:
            try:
                process_task(db_path, task)
            except Exception as e:
                print(f"[ERROR] Unhandled exception in extraction worker for task {task.get('file_hash')}: {e}")
                manager.complete_extraction(db_path, task['file_hash'], status='ERROR', error=str(e))
        else:
            print("No pending tasks. Sleeping...")
            interruptible_sleep(db_path, 10)
