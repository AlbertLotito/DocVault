from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.vault_manager import VaultManager, VaultStateError, VaultConflictError
from core.manager import get_db_path

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


class VaultSettingSet(BaseModel):
    value: str


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
            "UPDATE tasks SET status='PENDING', error=NULL WHERE vault_id=? AND status='ERROR'",
            (vault_id,)
        )
        conn.commit()
    return {"reset": cur.rowcount}


@router.post("/{vault_id}/gut")
def gut_vault(vault_id: str):
    """Transition vault to gutted state (wipes data)."""
    vm = _vm()
    v = vm.get_vault(vault_id)
    if not v:
        raise HTTPException(404, "Vault not found")
    
    # State machine enforcement: active -> archived -> gutted
    if v['state'] == 'active':
        vm.transition(vault_id, 'archived')
    
    try:
        vm.transition(vault_id, 'gutted')
        return {"status": "ok"}
    except VaultStateError as e:
        raise HTTPException(400, str(e))


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
        
        # Reload to get updated state
        v = vm.get_vault(vault_id)
        if v['state'] == 'archived':
            vm.transition(vault_id, 'gutted')
            
        vm.transition(vault_id, 'deleted')
        return {"status": "ok"}
    except VaultStateError as e:
        raise HTTPException(400, str(e))


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
