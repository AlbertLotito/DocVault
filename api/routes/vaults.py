import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.vault_manager import VaultManager, VaultStateError, VaultConflictError
from core.manager import get_db_path
from core.ingestor import parse_ignore_patterns

router = APIRouter(prefix="/api/vaults", tags=["vaults"])


class VaultCreate(BaseModel):
    name: str
    scan_directory: str
    priority: int = 5
    color: str = '#6366f1'


class VaultUpdate(BaseModel):
    name: str | None = None
    scan_directory: str | None = None
    priority: int | None = None
    color: str | None = None
    ignore_extensions: str | None = None
    ignore_folders: str | None = None


class VaultSettingSet(BaseModel):
    value: str


class PauseScanRequest(BaseModel):
    paused: bool


def _vm():
    return VaultManager(get_db_path())


@router.get("")
def list_vaults():
    return _vm().list_vaults()


@router.post("")
def create_vault(body: VaultCreate):
    try:
        return _vm().create_vault(body.name, body.scan_directory,
                                  body.priority, body.color)
    except VaultConflictError as e:
        raise HTTPException(409, str(e))


@router.get("/{vault_id}/ignore-preview")
def ignore_preview(vault_id: str):
    """Return count of already-indexed files matching this vault's current ignore rules."""
    import fnmatch as _fnmatch
    from core.manager import _connect
    from core.settings import settings as _settings
    vm = _vm()
    vault = vm.get_vault(vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")
    ignore_exts = (
        parse_ignore_patterns(_settings.get('ingestion:ignore_extensions') or '')
        | parse_ignore_patterns(vault.get('ignore_extensions', ''))
    )
    ignore_folders_set = (
        parse_ignore_patterns(_settings.get('ingestion:ignore_folders') or '')
        | parse_ignore_patterns(vault.get('ignore_folders', ''))
    )
    db_path = vm.db_path
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT file_hash, file_path FROM file_vault WHERE vault_id = ?",
            (vault_id,)
        ).fetchall()
    count = 0
    for row in rows:
        path = row['file_path'].replace('\\', '/')
        ext = os.path.splitext(path)[1].lower()
        parts = [p for p in path.split('/') if p]
        ext_match = ext in ignore_exts or ext.lstrip('.') in ignore_exts
        folder_match = ignore_folders_set and any(
            _fnmatch.fnmatch(p.lower(), pat)
            for p in parts[:-1]   # exclude filename itself
            for pat in ignore_folders_set
        )
        if ext_match or folder_match:
            count += 1
    return {"count": count}


@router.post("/{vault_id}/apply-ignore")
def apply_ignore(vault_id: str):
    """Remove already-indexed files matching this vault's current ignore rules.
    Removes file_vault membership. Purges tasks/FTS/images/Qdrant if no other vault claims the file.
    """
    import fnmatch as _fnmatch
    import sqlite3 as _sqlite3
    from core.manager import _connect
    from core.settings import settings as _settings
    vm = _vm()
    vault = vm.get_vault(vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Vault not found")
    ignore_exts = (
        parse_ignore_patterns(_settings.get('ingestion:ignore_extensions') or '')
        | parse_ignore_patterns(vault.get('ignore_extensions', ''))
    )
    ignore_folders_set = (
        parse_ignore_patterns(_settings.get('ingestion:ignore_folders') or '')
        | parse_ignore_patterns(vault.get('ignore_folders', ''))
    )
    db_path = vm.db_path

    # Pass 1: read all file_vault rows — quick SELECT, connection closed immediately
    with _connect(db_path) as conn:
        all_rows = conn.execute(
            "SELECT file_hash, file_path FROM file_vault WHERE vault_id = ?",
            (vault_id,)
        ).fetchall()

    # Determine which hashes match the ignore rules (pure memory work, no DB)
    to_remove = []
    for row in all_rows:
        path = row['file_path'].replace('\\', '/')
        ext = os.path.splitext(path)[1].lower()
        parts = [p for p in path.split('/') if p]
        ext_match = ext in ignore_exts or ext.lstrip('.') in ignore_exts
        folder_match = ignore_folders_set and any(
            _fnmatch.fnmatch(p.lower(), pat)
            for p in parts[:-1]
            for pat in ignore_folders_set
        )
        if ext_match or folder_match:
            to_remove.append(row['file_hash'])

    # Pass 2: delete each match in its own short transaction
    removed = 0
    for file_hash in to_remove:
        try:
            with _connect(db_path) as conn:
                conn.execute(
                    "DELETE FROM file_vault WHERE file_hash = ? AND vault_id = ?",
                    (file_hash, vault_id)
                )
                other = conn.execute(
                    "SELECT 1 FROM file_vault WHERE file_hash = ? LIMIT 1", (file_hash,)
                ).fetchone()
                if other is None:
                    conn.execute("DELETE FROM tasks WHERE file_hash = ?", (file_hash,))
                    conn.execute("DELETE FROM fts_index WHERE file_hash = ?", (file_hash,))
                    conn.execute("DELETE FROM extracted_images WHERE source_hash = ?", (file_hash,))
                conn.commit()
            if other is None:
                try:
                    from embeddings.vector_store import VectorStore
                    from core.settings import settings as _s2
                    vs = VectorStore(
                        host=_s2.get('qdrant:host'),
                        port=int(_s2.get('qdrant:port')),
                        collection='docvault',
                    )
                    vs.delete(file_hash)
                except Exception:
                    pass
            removed += 1
        except _sqlite3.OperationalError:
            continue  # skip if locked; partial removal is better than none
    return {"removed": removed}


@router.get("/{vault_id}")
def get_vault(vault_id: str):
    v = _vm().get_vault(vault_id)
    if not v:
        raise HTTPException(404, "Vault not found")
    return v


@router.put("/{vault_id}")
def update_vault(vault_id: str, body: VaultUpdate):
    return _vm().update_vault(vault_id, **body.model_dump(exclude_none=True))


@router.delete("/{vault_id}")
def delete_vault(vault_id: str, mode: str = 'archive'):
    state_map = {'archive': 'archived', 'gut': 'gutted', 'delete': 'deleted'}
    new_state = state_map.get(mode)
    if not new_state:
        raise HTTPException(400, f"mode must be one of: {list(state_map)}")
    try:
        return _vm().transition(vault_id, new_state)
    except VaultStateError as e:
        raise HTTPException(409, str(e))


@router.post("/{vault_id}/restore")
def restore_vault(vault_id: str):
    try:
        return _vm().restore(vault_id)
    except VaultStateError as e:
        raise HTTPException(409, str(e))


@router.post("/{vault_id}/reindex")
def reindex_vault(vault_id: str):
    _vm().reindex(vault_id)
    return {"status": "ok"}


@router.post("/{vault_id}/retry_errors")
def retry_errors(vault_id: str):
    """Reset all ERROR tasks in this vault back to PENDING."""
    from core.manager import get_db_path, _connect
    db_path = get_db_path()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='PENDING', error_log=NULL WHERE vault_id=? AND status='ERROR'",
            (vault_id,)
        )
        reset_count = cur.rowcount
        conn.commit()
    return {"reset": reset_count}


@router.post("/{vault_id}/retry_unsupported")
def retry_unsupported(vault_id: str):
    """Reset ERROR tasks whose error_log indicates unsupported format back to PENDING."""
    from core.manager import get_db_path, _connect
    db_path = get_db_path()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """UPDATE tasks SET status='PENDING', error_log=NULL
               WHERE vault_id=? AND status='ERROR'
               AND error_log LIKE 'Unsupported format:%'""",
            (vault_id,)
        )
        reset_count = cur.rowcount
        conn.commit()
    return {"reset": reset_count}


@router.post("/{vault_id}/gut")
def gut_vault(vault_id: str):
    """Transition vault to gutted state (wipes data)."""
    vm = _vm()
    v = vm.get_vault(vault_id)
    if not v:
        raise HTTPException(404, "Vault not found")

    try:
        if v['state'] == 'active':
            vm.transition(vault_id, 'archived')
        vm.transition(vault_id, 'gutted')
        return {"status": "ok"}
    except VaultStateError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Gut failed: {e}")


@router.post("/{vault_id}/delete")
def delete_vault(vault_id: str):
    """Transition vault to deleted state (wipes data + removes entry)."""
    vm = _vm()
    v = vm.get_vault(vault_id)
    if not v:
        raise HTTPException(404, "Vault not found")

    # State machine enforcement: active -> archived -> gutted -> deleted
    try:
        if v['state'] == 'active':
            vm.transition(vault_id, 'archived')

        v = vm.get_vault(vault_id)
        if v['state'] == 'archived':
            vm.transition(vault_id, 'gutted')

        vm.transition(vault_id, 'deleted')
        return {"status": "ok"}
    except VaultStateError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Delete failed: {e}")


@router.post("/{vault_id}/pause-scan")
def pause_vault_scan(vault_id: str, req: PauseScanRequest):
    try:
        vault = _vm().set_scan_paused(vault_id, req.paused)
        return vault
    except VaultStateError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{vault_id}/settings")
def get_vault_settings(vault_id: str):
    from core.settings import SettingsResolver, settings as gs
    r = SettingsResolver(vault_id=vault_id)
    return [
        {**meta, 'key': key, 'value': r.get(key)}
        for key, meta in gs.schema.items()
    ]


@router.post("/{vault_id}/settings")
def set_vault_setting(vault_id: str, key: str, body: VaultSettingSet):
    from core.manager import get_settings_db_path, _connect
    if key not in __import__('core.settings', fromlist=['settings']).settings.schema:
        raise HTTPException(400, f"Unknown setting key: {key}")
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vault_settings (vault_id, key, value) VALUES (?,?,?)",
            (vault_id, key, body.value)
        )
        conn.commit()
    return {"ok": True}


@router.delete("/{vault_id}/settings/{key}")
def delete_vault_setting(vault_id: str, key: str):
    from core.manager import get_settings_db_path, _connect
    with _connect(get_settings_db_path()) as conn:
        conn.execute(
            "DELETE FROM vault_settings WHERE vault_id=? AND key=?",
            (vault_id, key)
        )
        conn.commit()
    return {"ok": True}


@router.get("/{vault_id}/extractors")
def get_vault_extractors(vault_id: str):
    return _vm().get_vault_extractors(vault_id)


@router.post("/{vault_id}/extractors")
def set_vault_extractors(vault_id: str, body: list[dict]):
    _vm().set_vault_extractors(vault_id, body)
    return {"status": "ok"}
