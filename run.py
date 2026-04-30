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
import socket
from core import manager, ingestor, logger
from core.settings import settings
from workers import extraction_worker, embedding_worker, art_enrichment_worker

DB_PATH = manager.get_db_path()

def _make_embed_workers(db_path: str, concurrency: int) -> list:
    """Return watchdog entries for N embedding worker threads."""
    entries = []
    for i in range(max(1, concurrency)):
        wid = f"embed-{socket.gethostname()}-{os.getpid()}-{i}"
        entries.append((f"embedding-{i}", embedding_worker.run, (db_path, None, wid)))
    return entries

def ingestion_worker_run(db_path, interval_seconds=60):
    """Periodically scans all active vaults in parallel."""
    logger.info(f"Ingestion worker starting. Will scan every {interval_seconds}s.")
    while True:
        try:
            from core.vault_manager import VaultManager
            vm = VaultManager(db_path)
            vaults = vm.list_vaults()
            active = [v for v in vaults if v['state'] == 'active' and not v.get('scan_paused')]

            def _scan_vault(vault):
                try:
                    logger.info(f"Scanning vault '{vault['name']}': {vault['scan_directory']}")
                    ingestor.ingest(vault['scan_directory'], db_path, vault_id=vault['vault_id'], vault_row=vault)
                except Exception as e:
                    logger.error(f"Vault scan failed for '{vault['name']}': {e}")

            threads = [threading.Thread(target=_scan_vault, args=(v,), daemon=True) for v in active]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
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
    manager.init_settings_db()
    _rollback_optimizer_snapshot()
    manager.init_db(DB_PATH)
    manager.init_logs_db()
    n = manager.reset_stuck_tasks(DB_PATH)
    if n:
        logger.info(f"Startup: reset {n} orphaned PROCESSING/EMBEDDING task(s) to PENDING.")

    import configparser as _cp
    _cfg = _cp.ConfigParser(strict=False)
    _cfg.read(os.path.join(os.path.dirname(__file__), 'config.ini'))
    scan_dir = _cfg.get('paths', 'scan_directory', fallback='').strip()
    manager.bootstrap_default_vault(DB_PATH, scan_directory=scan_dir)

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
    embed_concurrency = max(1, int(settings.get('workers:embed_concurrency') or 1))
    managed_workers = [
        ('ingestion',   ingestion_worker_run,        (DB_PATH,)),
        ('extraction',  extraction_worker.run,        (DB_PATH, None)),
        ('art',         art_enrichment_worker.run,    (DB_PATH, None)),
    ]
    managed_workers.extend(_make_embed_workers(DB_PATH, embed_concurrency))
    t_watchdog = threading.Thread(
        target=_watchdog, args=(managed_workers, watchdog_interval),
        daemon=True, name='watchdog'
    )
    t_watchdog.start()

    # Start web server (blocking)
    host = settings.get('server:host') or '127.0.0.1'
    port = int(settings.get('server:port') or 8000)
    logger.critical(f"Starting DocVault at http://{host}:{port}  [PID {os.getpid()}]")
    config = uvicorn.Config(
        "api.main:app", host=host, port=port, reload=False,
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
    server.run()
    # Force-exit after server stops — daemon worker threads may still be
    # blocked on Ollama calls and would prevent a clean interpreter shutdown.
    os._exit(0)



if __name__ == "__main__":
    # On Windows, suppress the console-close event so closing the terminal
    # doesn't silently kill the process mid-task. The process still exits on
    # Ctrl+C (SIGINT) which uvicorn handles gracefully.
    try:
        import ctypes, ctypes.wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.SetConsoleCtrlHandler(None, False)  # restore default Ctrl+C
        HANDLER = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)
        def _ctrl_handler(event):
            if event in (2, 5, 6):  # CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN
                logger.critical("Console close event received — ignoring (use Ctrl+C to stop)")
                return True  # suppress: don't let Windows kill the process immediately
            return False
        _ctrl_cb = HANDLER(_ctrl_handler)
        kernel32.SetConsoleCtrlHandler(_ctrl_cb, True)
    except Exception:
        pass  # non-Windows or ctypes unavailable — no-op

    start()
