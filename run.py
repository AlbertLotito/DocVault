"""
DocVault entry point.
Starts the FastAPI web server and spawns background workers in threads.
"""
import json
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

def _rollback_optimizer_snapshot():
    """
    If a previous optimizer run crashed mid-sweep, tuning:_optimizer_snapshot
    will still be in settings.db with the original settings. Restore them.
    This must run after init_settings_db() and before any workers start.
    """
    from core.manager import get_settings_db_path, _connect
    try:
        with _connect(get_settings_db_path()) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='tuning:_optimizer_snapshot'"
            ).fetchone()
            if not row:
                return
            try:
                snapshot = json.loads(row['value'])
            except json.JSONDecodeError as exc:
                conn.execute("DELETE FROM settings WHERE key='tuning:_optimizer_snapshot'")
                conn.commit()
                logger.warning(f"Startup: corrupt optimizer snapshot cleared (parse error: {exc}). Settings were NOT restored.")
                return
            from core.settings import settings as _settings
            for k, v in snapshot.items():
                try:
                    _settings.set(k, v)
                except Exception:
                    pass  # skip keys not in schema
            conn.execute("DELETE FROM settings WHERE key='tuning:_optimizer_snapshot'")
            conn.commit()
            logger.warning("Startup: restored settings from stale optimizer snapshot and cleared it.")
    except Exception as e:
        logger.error(f"Startup: optimizer snapshot rollback failed: {e}")


def _watchdog(workers: list, interval: int = 30):
    """
    Monitor worker threads and restart any that have died.
    Each entry in workers is (name, target_fn, args_tuple).
    Runs as a daemon thread — never raises.
    """
    live = {}
    for name, target, args in workers:
        t = threading.Thread(target=target, args=args, daemon=True, name=name)
        t.start()
        live[name] = (t, target, args)
        logger.info(f"Watchdog: started {name}")

    while True:
        time.sleep(interval)
        for name, (t, target, args) in list(live.items()):
            if not t.is_alive():
                logger.error(f"Watchdog: {name} thread died — restarting")
                # Reset any tasks the dead thread was holding in PROCESSING or EMBEDDING
                try:
                    n = manager.reset_stuck_tasks(DB_PATH)
                    if n:
                        logger.info(f"Watchdog: reset {n} orphaned task(s) after {name} death")
                except Exception as e:
                    logger.error(f"Watchdog: could not reset stuck tasks: {e}")
                try:
                    from core.alerts import send_alert
                    send_alert(
                        title=f"Worker restarted: {name}",
                        message=f"DocVault watchdog detected that '{name}' died and restarted it automatically.",
                        level='warning',
                        source='watchdog',
                    )
                except Exception:
                    pass
                try:
                    new_t = threading.Thread(target=target, args=args, daemon=True, name=name)
                    new_t.start()
                    live[name] = (new_t, target, args)
                    logger.info(f"Watchdog: {name} restarted successfully")
                except Exception as start_err:
                    logger.error(
                        f"Watchdog: could not restart {name}: {start_err}. "
                        f"Will retry in {interval}s."
                    )
                    # Leave dead entry in live{} so we retry next interval


def start():
    # Init DBs in order — settings.db first (survives resets), then main DB, then logs
    manager.init_settings_db()
    _rollback_optimizer_snapshot()
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

    # Register/Certify System Kernels
    from core.registry import RegistryManager
    rm = RegistryManager()
    rm.register_system_kernels()

    # Start resource governor daemon (not under watchdog — it has its own safe loop)
    from core.monitor import HardwareMonitor
    monitor = HardwareMonitor()
    t_monitor = threading.Thread(target=monitor.run, daemon=True, name='monitor')
    t_monitor.start()

    # Start all workers under the watchdog so dead threads are automatically restarted
    watchdog_interval = int(settings.get('monitor:watchdog_interval') or 30)
    managed_workers = [
        ('ingestion',   ingestion_worker_run,        (DB_PATH,)),
        ('extraction',  extraction_worker.run,        (DB_PATH, None)),
        ('embedding',   embedding_worker.run,         (DB_PATH, None)),
        ('art',         art_enrichment_worker.run,    (DB_PATH, None)),
    ]
    t_watchdog = threading.Thread(
        target=_watchdog, args=(managed_workers, watchdog_interval),
        daemon=True, name='watchdog'
    )
    t_watchdog.start()

    # Start web server (blocking)
    logger.critical("Starting DocVault at http://localhost:8000")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)



if __name__ == "__main__":
    start()
