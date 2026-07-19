"""
core/vault_manager.py -- Vault CRUD, state machine, and settings resolution.

Vault state machine (API-enforced):
    active -> archived -> gutted -> deleted
    archived -> active  (restore)

Source files on disk are NEVER touched.
"""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from core.manager import _connect, get_db_path


class VaultStateError(Exception):
    """Raised when a state transition is invalid."""


class VaultConflictError(Exception):
    """Raised when a vault constraint is violated (e.g. duplicate scan_directory)."""


# Valid transitions: from_state -> [allowed to_states]
_TRANSITIONS = {
    'active':   ['archived'],
    'archived': ['active', 'gutted'],
    'gutted':   ['deleted'],
    'deleted':  [],
}


class VaultManager:

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_db_path()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def list_vaults(self, include_deleted: bool = False) -> list[dict]:
        with _connect(self.db_path) as conn:
            if include_deleted:
                rows = conn.execute("SELECT * FROM vaults ORDER BY name").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM vaults WHERE state != 'deleted' ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_vault(self, vault_id: str) -> dict | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM vaults WHERE vault_id = ?", (vault_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_vault(self, name: str, scan_directory: str,
                     priority: int = 5, color: str = '#6366f1') -> dict:
        # Guard against duplicate scan directories
        with _connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT vault_id FROM vaults WHERE scan_directory = ? AND state != 'deleted'",
                (scan_directory,)
            ).fetchone()
            if existing:
                raise VaultConflictError(
                    f"A vault already watches '{scan_directory}'"
                )

            vault_id = str(uuid.uuid4())
            now      = self._now()
            conn.execute(
                """INSERT INTO vaults
                   (vault_id, name, scan_directory, priority, color, state, created_at, updated_at)
                   VALUES (?,?,?,?,?,'active',?,?)""",
                (vault_id, name, scan_directory, priority, color, now, now)
            )
            conn.commit()
        return self.get_vault(vault_id)

    def update_vault(self, vault_id: str, **fields) -> dict:
        allowed = {'name', 'scan_directory', 'priority', 'color', 'ignore_extensions', 'ignore_folders'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_vault(vault_id)
        updates['updated_at'] = self._now()
        set_clause = ', '.join(f"{k} = ?" for k in updates)
        with _connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE vaults SET {set_clause} WHERE vault_id = ?",
                list(updates.values()) + [vault_id]
            )
            conn.commit()
        return self.get_vault(vault_id)

    def transition(self, vault_id: str, new_state: str) -> dict:
        vault = self.get_vault(vault_id)
        if not vault:
            raise VaultStateError(f"Vault {vault_id} not found")

        current = vault['state']
        allowed = _TRANSITIONS.get(current, [])
        if new_state not in allowed:
            raise VaultStateError(
                f"Cannot transition vault from '{current}' to '{new_state}'. "
                f"Allowed: {allowed}"
            )

        if new_state == 'gutted':
            self._gut_vault(vault_id)

        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE vaults SET state=?, updated_at=? WHERE vault_id=?",
                (new_state, self._now(), vault_id)
            )
            conn.commit()
        return self.get_vault(vault_id)

    def _gut_vault(self, vault_id: str):
        """
        Wipe all extracted content for this vault. Batch-delete of tasks/FTS/
        images/file_vault rows is shared with the missing-files purge feature
        via manager.purge_file_hashes; vector cleanup stays a local, best-effort
        step since it's the only part specific to this caller.
        """
        with _connect(self.db_path) as conn:
            hashes = [r[0] for r in conn.execute(
                "SELECT file_hash FROM tasks WHERE vault_id = ?", (vault_id,)
            ).fetchall()]

        # Note: purge_file_hashes removes file_vault rows for these hashes
        # across ALL vaults, not just this one -- if a hash is also registered
        # in another vault (cross-vault content dedup), that vault's file_vault
        # row is removed too. This is schema-forced: file_vault.file_hash
        # references tasks(file_hash) with no ON DELETE CASCADE, and the tasks
        # row for these hashes is being deleted regardless (they belong to
        # this vault). The other vault will simply re-register the file as
        # new content on its next scan -- same eventual outcome, no data loss.
        from core.manager import purge_file_hashes
        purge_file_hashes(self.db_path, hashes)

        # Remove vectors (best-effort — never blocks the delete).
        if hashes:
            try:
                from embeddings.vector_store import VectorStore
                VectorStore().delete_by_hashes(hashes)
            except Exception as e:
                print(f"[vault_manager] Warning: could not remove vectors: {e}")

    def get_vault_extractors(self, vault_id: str) -> list[dict]:
        """Returns the custom extractor config for a vault, or empty list if using defaults."""
        from core.manager import get_settings_db_path
        with _connect(get_settings_db_path()) as conn:
            row = conn.execute(
                "SELECT value FROM vault_settings WHERE vault_id = ? AND key = 'vault:extractor_config'",
                (vault_id,)
            ).fetchone()
            if row:
                try:
                    return json.loads(row['value'])
                except:
                    return []
        return []

    def set_vault_extractors(self, vault_id: str, config: list[dict]):
        """Saves custom extractor config (list of {name, priority, enabled})."""
        from core.manager import get_settings_db_path
        with _connect(get_settings_db_path()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vault_settings (vault_id, key, value) VALUES (?, 'vault:extractor_config', ?)",
                (vault_id, json.dumps(config))
            )
            conn.commit()

    def restore(self, vault_id: str) -> dict:
        """Convenience: archived -> active."""
        return self.transition(vault_id, 'active')

    def set_scan_paused(self, vault_id: str, paused: bool) -> dict:
        vault = self.get_vault(vault_id)
        if not vault:
            raise VaultStateError(f"Vault {vault_id!r} not found")
        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE vaults SET scan_paused = ?, updated_at = ? WHERE vault_id = ?",
                (1 if paused else 0, self._now(), vault_id)
            )
            conn.commit()
        return self.get_vault(vault_id)

    def reindex(self, vault_id: str):
        """Wipe vectors and reset COMPLETED tasks to EXTRACTED for this vault only."""
        with _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status='EXTRACTED' WHERE vault_id=? AND status='COMPLETED'",
                (vault_id,)
            )
            conn.commit()
        # Remove vectors for this vault so they are re-embedded from scratch.
        try:
            from embeddings.vector_store import VectorStore
            import sqlite3
            with sqlite3.connect(self.db_path) as _c:
                _hashes = [r[0] for r in _c.execute(
                    "SELECT file_hash FROM tasks WHERE vault_id=?", (vault_id,)
                ).fetchall()]
            if _hashes:
                VectorStore().delete_by_hashes(_hashes)
        except Exception as e:
            print(f"[vault_manager] Warning: vector removal failed: {e}")
