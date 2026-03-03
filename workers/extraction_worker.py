import os, socket, time, threading
from core import manager, router
from core.extractors.base import LegacyExtractorAdapter, ExtractorContext, ExtractorLogger
from core.settings import SettingsResolver
from extractors import image_extractor, unknown_extractor
from workers.utils import interruptible_sleep, should_pause_or_throttle, get_throttle_sleep


def _build_context(task: dict) -> ExtractorContext:
    vault_id  = task.get('vault_id') or ''
    file_hash = task['file_hash']
    return ExtractorContext(
        vault_id     = vault_id,
        file_hash    = file_hash,
        cancel_token = threading.Event(),
        logger       = ExtractorLogger('extraction_worker', vault_id, file_hash),
        settings     = SettingsResolver(vault_id=vault_id),
        timeout_secs = None,
    )


def process_task(db_path, task):
    file_hash = task['file_hash']
    file_path = task['file_path']
    file_type = task['file_type'] or ''

    ctx = _build_context(task)
    ctx.logger.info(f"Starting extraction: {os.path.basename(file_path)}")

    extractors = router.get_extractors(file_type)

    if extractors == [unknown_extractor]:
        _, msg = unknown_extractor.extract(file_path)
        manager.complete_extraction(db_path, file_hash, status='UNKNOWN', error=msg)
        ctx.logger.info(f"Flagged as UNKNOWN: {file_type}")
        return

    errors, combined_text, combined_metadata = [], [], {}
    combined_images = []

    for ext_module in extractors:
        adapter = LegacyExtractorAdapter(ext_module)
        result  = adapter.run(file_path, ctx)

        if result.status == 'cancelled':
            ctx.logger.warning(f"Extractor {adapter.name} cancelled")
            manager.complete_extraction(db_path, file_hash, status='PENDING', error=None)
            return

        for e in result.errors:
            errors.append(f"[{e.extractor_name}] {e.message}")

        if result.text:
            combined_text.append(result.text)
        if result.metadata:
            combined_metadata.update(result.metadata)
        for img in result.images:
            manager.insert_extracted_image(db_path, file_hash, {
                'file_path':   img.file_path,
                'page_num':    img.page_num,
                'image_index': img.image_index,
                'width':       img.width,
                'height':      img.height,
            })
            combined_images.append(img)

        # Log timing to logs.db
        _record_timing(task, adapter.name, result.elapsed_secs)

    final_text   = "\n\n".join(combined_text) if combined_text else None
    has_content  = bool(final_text or combined_metadata)
    final_status = 'ERROR' if (errors and not has_content) else 'EXTRACTED'

    manager.complete_extraction(
        db_path, file_hash, status=final_status,
        text=final_text,
        metadata=combined_metadata if combined_metadata else None,
        error=' | '.join(errors) if errors else None,
    )
    ctx.logger.info(f"Extraction complete: {final_status}")


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


def run(db_path, worker_id=None, shutdown_event=None):
    if worker_id is None:
        worker_id = f"extract-{socket.gethostname()}-{os.getpid()}"
    print(f"Extraction worker starting. ID: {worker_id}")

    while True:
        skip, reason = should_pause_or_throttle()
        if skip:
            print(f"Extraction worker {reason}. Sleeping...")
            interruptible_sleep(db_path, 10)
            continue

        extra = get_throttle_sleep(reason)
        if extra:
            time.sleep(extra)

        task = manager.claim_pending_task(db_path, worker_id)
        if task:
            try:
                process_task(db_path, task)
            except Exception as e:
                print(f"[ERROR] Extraction worker unhandled: {e}")
                manager.complete_extraction(db_path, task['file_hash'],
                                            status='ERROR', error=str(e))
        else:
            interruptible_sleep(db_path, 10)
