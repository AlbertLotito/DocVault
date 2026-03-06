"""
DocVault Kernel Registry Manager.
Manages discovery, installation, and integrity verification of extractors.
"""
import os
import json
import importlib.util
from datetime import datetime, timezone
from core.manager import get_settings_db_path, _connect
from core.certification import ContractAuditor, calculate_file_hash
from core import logger, alerts

EXTRACTORS_DIR = os.path.join(os.path.dirname(__file__), '..', 'extractors')

class RegistryManager:
    def __init__(self):
        self.db_path = get_settings_db_path()

    def sync_disk_to_db(self):
        """
        Scans /extractors and updates the registry.
        - Identifies new kernels (Unverified).
        - Detects tampered kernels (Hash mismatch).
        - Flags orphans (DB entry but no file).
        """
        disk_files = [f for f in os.listdir(EXTRACTORS_DIR) 
                      if (f.endswith('_extractor.py') or f.endswith('.json')) and not f.startswith('_')]
        
        with _connect(self.db_path) as conn:
            # 1. Get all registered kernels (Handle missing table during bootstrap)
            try:
                rows = conn.execute("SELECT kernel_id, file_hash, module_name FROM ext_registry").fetchall()
                db_kernels = {r['kernel_id']: r for r in rows}
            except Exception:
                # Table doesn't exist yet, nothing to sync
                return

            seen_ids = set()

            for filename in disk_files:
                module_name = filename.rsplit('.', 1)[0]
                file_path = os.path.join(EXTRACTORS_DIR, filename)
                is_json = filename.endswith('.json')
                
                try:
                    # Peek at the manifest
                    manifest = self._peek_manifest(file_path, module_name, is_json)
                    if not manifest:
                        logger.warn(f"Skipping {filename}: No MANIFEST found.", ext="registry")
                        continue

                    kid = manifest['id']
                    current_hash = calculate_file_hash(file_path)
                    seen_ids.add(kid)
                    
                    kernel_type = manifest.get('type', 'python')
                    target_type = manifest.get('target_type', 'file')
                    launch_config = json.dumps(manifest.get('command', [])) if kernel_type == 'subprocess' else None

                    if kid not in db_kernels:
                        # NEW KERNEL
                        logger.info(f"New kernel discovered: {kid} ({filename})", ext="registry")
                        alerts.send_alert(
                            "New Kernel Discovered", 
                            f"Kernel '{manifest.get('name', module_name)}' ({kid}) is pending certification.",
                            level='info', source='registry'
                        )
                        conn.execute(
                            """INSERT INTO ext_registry (kernel_id, module_name, version, file_hash, extensions, status, kernel_type, launch_config, target_type)
                               VALUES (?, ?, ?, ?, ?, 'unverified', ?, ?, ?)""",
                            (kid, module_name, manifest['version'], current_hash, json.dumps(manifest['extensions']), kernel_type, launch_config, target_type)
                        )
                    else:
                        # EXISTING KERNEL - Check Integrity
                        if db_kernels[kid]['file_hash'] != current_hash:
                            logger.warn(f"SECURITY ALERT: Kernel {kid} tampered or updated on disk. Disabling.", ext="registry")
                            alerts.send_alert(
                                "Security Violation: Kernel Tampered", 
                                f"Kernel '{kid}' has been modified on disk and was automatically disabled for safety.",
                                level='critical', source='registry'
                            )
                            conn.execute(
                                "UPDATE ext_registry SET status = 'tampered', is_enabled = 0, last_seen_at = CURRENT_TIMESTAMP WHERE kernel_id = ?",
                                (kid,)
                            )
                        else:
                            # Healthy
                            conn.execute(
                                "UPDATE ext_registry SET last_seen_at = CURRENT_TIMESTAMP WHERE kernel_id = ?",
                                (kid,)
                            )
                except Exception as e:
                    logger.error(f"Error syncing {filename}: {e}", ext="registry")

            # 2. Flag Orphans
            for kid in db_kernels:
                if kid not in seen_ids:
                    conn.execute("UPDATE ext_registry SET status = 'missing' WHERE kernel_id = ?", (kid,))

            conn.commit()

    def _peek_manifest(self, file_path: str, module_name: str, is_json: bool = False) -> dict | None:
        """Loads the module or JSON file and extracts the MANIFEST."""
        try:
            if is_json:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return getattr(mod, 'MANIFEST', None)
        except Exception:
            return None

    def register_system_kernels(self):
        """
        Special bootstrap: Automatically certifies and registers the built-in 
        DocVault suite to ensure immediate functionality.
        """
        self.sync_disk_to_db()
        with _connect(self.db_path) as conn:
            # Force status to 'certified' and enable for all core IDs
            conn.execute(
                """UPDATE ext_registry SET status = 'certified', is_enabled = 1, certified_at = CURRENT_TIMESTAMP
                   WHERE kernel_id LIKE 'com.docvault.%' OR kernel_id LIKE 'com.microsoft.%' OR kernel_id LIKE 'com.openai.%' OR kernel_id LIKE 'com.google.%'"""
            )
            conn.commit()
        logger.info("System kernels certified and registered.", ext="registry")

    def get_active_kernels(self) -> list[dict]:
        """Returns only enabled and certified kernels."""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM ext_registry WHERE status = 'certified' AND is_enabled = 1"
            ).fetchall()
            return [dict(r) for r in rows]
