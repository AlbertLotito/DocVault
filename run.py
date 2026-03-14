"""
DocVault entry point.
Starts the FastAPI web server and spawns background workers in threads.
"""
import threading
import time
import uvicorn
import configparser
import os
from core import manager, ingestor, logger
from core.settings import settings
from workers import extraction_worker, embedding_worker, art_enrichment_worker

DB_PATH = manager.get_db_path()

def ingestion_worker_run(db_path, interval_seconds=60):
    """Periodically scans all active vaults."""
    logger.info(f"Ingestion worker starting. Will scan every {interval_seconds}s.")
    while True:
        try:
            from core.vault_manager import VaultManager
            vm = VaultManager(db_path)
            vaults = vm.list_vaults()
            active = [v for v in vaults if v['state'] == 'active']
            for vault in active:
                scan_dir = vault['scan_directory']
                vault_id = vault['vault_id']
                logger.info(f"Scanning vault '{vault['name']}': {scan_dir}")
                ingestor.ingest(scan_dir, db_path, vault_id=vault_id)
        except Exception as e:
            logger.error(f"Ingestion worker failed: {e}")
        time.sleep(interval_seconds)

def start():
    # Init DBs in order — settings.db first (survives resets), then main DB, then logs
    manager.init_settings_db()
    manager.init_db(DB_PATH)
    manager.init_logs_db()

    # Reset any tasks left in PROCESSING or EMBEDDING from the previous process.
    # These are always orphaned on startup — the workers that claimed them are gone.
    n = manager.reset_stuck_tasks(DB_PATH)
    if n:
        logger.info(f"Startup: reset {n} orphaned PROCESSING/EMBEDDING task(s) to PENDING.")

    # Bootstrap default vault on first run or migration from pre-vault version
    scan_dir = settings.get('paths:scan_directory')
    manager.bootstrap_default_vault(DB_PATH, scan_directory=scan_dir)

    # 2. Register/Certify System Kernels
    from core.registry import RegistryManager
    rm = RegistryManager()
    rm.register_system_kernels()

    # Start resource governor daemon
    from core.monitor import HardwareMonitor
    monitor = HardwareMonitor()
    t_monitor = threading.Thread(target=monitor.run, daemon=True, name='monitor')
    t_monitor.start()

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

    # Start art enrichment worker (always; worker self-gates if nothing is configured)
    t_art = threading.Thread(
        target=art_enrichment_worker.run, args=(DB_PATH, None), daemon=True
    )
    t_art.start()

    # Start web server (blocking)
    logger.critical("Starting DocVault at http://localhost:8000")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)



if __name__ == "__main__":
    start()
