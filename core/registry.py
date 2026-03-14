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
        """
        # Improved Filter: Catch fallback_kernel and any valid .py/.json
        disk_files = [f for f in os.listdir(EXTRACTORS_DIR) 
                      if (f.endswith('.py') or f.endswith('.json')) and not f.startswith('_')]
        
        with _connect(self.db_path) as conn:
            # 1. Get all registered kernels (Handle missing table during bootstrap)
            try:
                rows = conn.execute("SELECT kernel_id, file_hash, module_name, status FROM ext_registry").fetchall()
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
                    description = manifest.get('description') or ''

                    if kid not in db_kernels:
                        # NEW KERNEL
                        logger.info(f"New kernel discovered: {kid} ({filename})", ext="registry")
                        alerts.send_alert(
                            "New Kernel Discovered", 
                            f"Kernel '{manifest.get('name', module_name)}' ({kid}) is pending certification.",
                            level='info', source='registry'
                        )
                        conn.execute(
                            """INSERT INTO ext_registry (kernel_id, module_name, version, file_hash, extensions, status, kernel_type, launch_config, target_type, description)
                               VALUES (?, ?, ?, ?, ?, 'unverified', ?, ?, ?, ?)""",
                            (kid, module_name, manifest['version'], current_hash, json.dumps(manifest['extensions']), kernel_type, launch_config, target_type, description)
                        )
                    else:
                        # EXISTING KERNEL - Check Integrity
                        row = db_kernels[kid]
                        db_hash = row['file_hash']
                        db_status = row['status'] or 'unverified'

                        if db_hash != current_hash:
                            if db_status == 'certified':
                                logger.warn(f"SECURITY ALERT: Certified kernel {kid} tampered on disk. Disabling.", ext="registry")
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
                                # Not certified yet - allow updates to flow through
                                conn.execute(
                                    """UPDATE ext_registry 
                                       SET file_hash = ?, version = ?, extensions = ?, description = ?, 
                                           last_seen_at = CURRENT_TIMESTAMP, status = 'unverified'
                                       WHERE kernel_id = ?""",
                                    (current_hash, manifest['version'], json.dumps(manifest['extensions']), description, kid)
                                )
                        else:
                            # Healthy - Update metadata if changed
                            conn.execute(
                                """UPDATE ext_registry 
                                   SET last_seen_at = CURRENT_TIMESTAMP, 
                                       description = ?, 
                                       extensions = ?, 
                                       version = ? 
                                   WHERE kernel_id = ?""",
                                (description, json.dumps(manifest['extensions']), manifest['version'], kid)
                            )
                except Exception as e:
                    logger.error(f"Error syncing {filename}: {e}", ext="registry")

            # 2. Flag Orphans
            for kid in db_kernels:
                if kid not in seen_ids:
                    conn.execute("UPDATE ext_registry SET status = 'missing' WHERE kernel_id = ?", (kid,))

            conn.commit()

    def certify_kernel(self, kernel_id: str):
        """
        Formally certifies a kernel by blessing its CURRENT disk hash.
        This is called when a user clicks 'Activate' in the Lab.
        """
        # 1. Sync first to ensure we have the absolute latest disk state in the DB
        self.sync_disk_to_db()

        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT module_name, file_hash FROM ext_registry WHERE kernel_id = ?", (kernel_id,)).fetchone()
            if not row: raise ValueError(f"Kernel {kernel_id} not found in registry.")
            
            # The hash in the DB is now guaranteed to be the current disk hash thanks to sync_disk_to_db()
            current_disk_hash = row['file_hash']
            
            conn.execute(
                """UPDATE ext_registry 
                   SET status = 'certified', is_enabled = 1, file_hash = ?, certified_at = CURRENT_TIMESTAMP 
                   WHERE kernel_id = ?""",
                (current_disk_hash, kernel_id)
            )
            conn.commit()
            logger.info(f"Kernel {kernel_id} formally certified and enabled.", ext="registry")

    def decertify_kernel(self, kernel_id: str):
        """
        Removes certification from a kernel, setting it back to 'unverified'.
        This is called when a user clicks 'Decertify' in the Lab.
        """
        with _connect(self.db_path) as conn:
            conn.execute(
                """UPDATE ext_registry
                   SET status = 'unverified', is_enabled = 0
                   WHERE kernel_id = ?""",
                (kernel_id,)
            )
            conn.commit()
            logger.info(f"Kernel {kernel_id} decertified and disabled.", ext="registry")

    def _peek_manifest(self, file_path: str, module_name: str, is_json: bool = False) -> dict | None:
        """
        Extracts the MANIFEST and description without executing the Python code.
        Uses AST for safe, side-effect-free inspection.
        """
        try:
            if is_json:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            
            import ast
            with open(file_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())
            
            manifest = None
            description = ast.get_docstring(tree) or ""
            
            for node in tree.body:
                # Look for MANIFEST = {...}
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'MANIFEST':
                            # Convert the AST dict to a real Python dict
                            manifest = ast.literal_eval(node.value)
                
                # Look for __description__ = "..."
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == '__description__':
                            if isinstance(node.value, (ast.Constant, ast.Str)):
                                description = getattr(node.value, 'value', getattr(node.value, 's', ''))

            if manifest:
                if not manifest.get('description'):
                    manifest['description'] = description
                return manifest
                
            return None
        except Exception as e:
            logger.error(f"AST Peek failed for {module_name}: {e}", ext="registry")
            return None

    def register_system_kernels(self):
        """
        Special bootstrap: Automatically certifies and registers the built-in 
        DocVault suite to ensure immediate functionality.
        Only applies to kernels that are currently 'missing' or brand new.
        If a kernel was manually decertified, it remains decertified.
        """
        self.sync_disk_to_db()
        with _connect(self.db_path) as conn:
            # Force status to 'certified' only if they are currently marked as missing or have no certified_at date
            conn.execute(
                """UPDATE ext_registry 
                   SET status = 'certified', is_enabled = 1, certified_at = CURRENT_TIMESTAMP
                   WHERE (status = 'missing' OR certified_at IS NULL)
                   AND (kernel_id LIKE 'com.docvault.%' OR kernel_id LIKE 'com.microsoft.%' OR kernel_id LIKE 'com.openai.%' OR kernel_id LIKE 'com.google.%')"""
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
