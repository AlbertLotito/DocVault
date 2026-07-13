import os
import threading
from fastapi import APIRouter, Query
from pydantic import BaseModel
from core import manager
from core.vault_manager import VaultManager
from workers.art_enrichment_worker import _nfo_path, _read_nfo

router = APIRouter()

_rescan_lock = threading.Lock()
_rescan_running = False


def _db_path() -> str:
    from api.main import DB_PATH
    return DB_PATH


@router.get("/art/issues")
def list_issues(status: str = None, vault_id: str = None, q: str = None,
                 limit: int = 50, offset: int = 0):
    return manager.list_art_issues(_db_path(), status=status, vault_id=vault_id,
                                    q=q, limit=limit, offset=offset)


class IdsRequest(BaseModel):
    ids: list[int] = []


@router.post("/art/issues/reprocess")
def reprocess_issues(body: IdsRequest):
    """Delete the .nfo (and the tracking row) for each selected issue, so the
    worker's normal unprocessed-file scan picks them up fresh next cycle."""
    db = _db_path()
    rows = manager.get_art_issues_by_ids(db, body.ids)
    cleared = 0
    for row in rows:
        nfo = _nfo_path(row['file_path'])
        if os.path.exists(nfo):
            try:
                os.remove(nfo)
                cleared += 1
            except OSError:
                pass
    deleted = manager.delete_art_issues(db, body.ids)
    return {'ok': True, 'nfo_removed': cleared, 'rows_removed': deleted}


class DismissRequest(BaseModel):
    ids: list[int] = []
    all: bool = False
    status: str = None
    vault_id: str = None
    q: str = None


@router.post("/art/issues/dismiss")
def dismiss_issues(body: DismissRequest):
    """Remove tracking rows only — .nfo files and images are untouched."""
    db = _db_path()
    if body.all:
        deleted = manager.delete_art_issues_by_filter(
            db, status=body.status, vault_id=body.vault_id, q=body.q
        )
    else:
        deleted = manager.delete_art_issues(db, body.ids)
    return {'ok': True, 'rows_removed': deleted}


def _scan_vault_for_issues(db_path: str):
    global _rescan_running
    try:
        vm = VaultManager(db_path)
        vaults = [v for v in vm.list_vaults() if v['state'] in ('active', 'archived')]
        found = 0
        for vault in vaults:
            vault_dir = vault['scan_directory']
            if not os.path.isdir(vault_dir):
                continue
            for root, dirs, files in os.walk(vault_dir):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for name in files:
                    if not name.endswith('.nfo'):
                        continue
                    image_path = os.path.join(root, name[:-len('.nfo')])
                    data = _read_nfo(image_path)
                    renamed_to = data.get('renamed_to', '')
                    if renamed_to == '(failed)':
                        status = 'failed'
                    elif renamed_to == '(low confidence — manual review)':
                        status = 'review'
                    else:
                        continue  # resolved (renamed) or unrecognised — not an issue
                    try:
                        confidence = float(data.get('confidence', 0) or 0)
                    except (TypeError, ValueError):
                        confidence = 0.0
                    manager.upsert_art_issue(
                        db_path, image_path, vault['vault_id'], status,
                        data.get('source', ''), data.get('artist', ''),
                        data.get('title', ''), confidence, data.get('error', '')
                    )
                    found += 1
        from core import logger
        logger.info(f"[art-rescan] Backfill complete — {found} issue(s) found/refreshed.", ext="art")
    except Exception as e:
        from core import logger
        logger.error(f"[art-rescan] Backfill failed: {e}", ext="art")
    finally:
        with _rescan_lock:
            _rescan_running = False


@router.post("/art/issues/rescan")
def rescan_issues():
    """Walk all vaults' .nfo sidecars and (re)populate the issues table.
    Runs in a background thread — can take minutes on a large vault."""
    global _rescan_running
    with _rescan_lock:
        if _rescan_running:
            return {'ok': True, 'status': 'already_running'}
        _rescan_running = True
    t = threading.Thread(target=_scan_vault_for_issues, args=(_db_path(),), daemon=True)
    t.start()
    return {'ok': True, 'status': 'started'}


@router.get("/art/issues/rescan/status")
def rescan_status():
    with _rescan_lock:
        return {'running': _rescan_running}
