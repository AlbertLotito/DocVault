"""
DocVault entry point.
Starts the FastAPI web server and spawns background workers in threads.
"""
import threading
import time
import uvicorn
import configparser
import os
from core import manager, ingestor
from core.settings import settings
from workers import extraction_worker, embedding_worker

DB_PATH = manager.get_db_path()

def ingestion_worker_run(db_path, interval_seconds=60):
    """Periodically scans the directory for new files."""
    print(f"Ingestion worker starting. Will scan every {interval_seconds}s.")
    # This loop will be terminated abruptly when the main thread exits
    while True:
        try:
            scan_dir = settings.get('paths:scan_directory')
            print(f"Starting periodic scan of: {scan_dir}")
            ingestor.ingest(scan_dir, db_path)
            print(f"Scan complete. Sleeping for {interval_seconds}s.")
        except Exception as e:
            print(f"[ERROR] Ingestion worker failed: {e}")
        time.sleep(interval_seconds)

def start():
    # Init DBs in order — settings.db first (survives resets), then main DB, then logs
    manager.init_settings_db()
    manager.init_db(DB_PATH)
    manager.init_logs_db()

    # Bootstrap default vault on first run or migration from pre-vault version
    scan_dir = settings.get('paths:scan_directory')
    manager.bootstrap_default_vault(DB_PATH, scan_directory=scan_dir)

    # Start ingestion worker in a daemon thread
    t_ingest = threading.Thread(
        target=ingestion_worker_run, args=(DB_PATH,), daemon=True
    )
    t_ingest.start()

    # Start extraction worker in a daemon thread
    t_extract = threading.Thread(
        target=extraction_worker.run, args=(DB_PATH, None), daemon=True # Pass None for shutdown_event
    )
    t_extract.start()

    # Start embedding worker in a daemon thread
    t_embed = threading.Thread(
        target=embedding_worker.run, args=(DB_PATH, None), daemon=True # Pass None for shutdown_event
    )
    t_embed.start()

    # Start web server (blocking)
    print("Starting DocVault at http://localhost:8000")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
