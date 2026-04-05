# Per-Vault Scan Pause Design

## Goal

Allow individual vaults to have their ingestion scanning paused and resumed independently, without affecting other vaults or the global worker pause state.

## Background

DocVault's ingestion worker scans all `active` vaults on a 60-second cycle. The only existing pause mechanism is a global toggle (`settings.paused`) that halts all workers (extraction, embedding, and ingestion) for all vaults. There is no way to stop scanning a single vault — for example, a NAS vault that is temporarily unreachable — without pausing the entire system.

## Architecture

A `scan_paused` boolean column is added to the `vaults` table. The ingestion worker skips vaults where `scan_paused = 1`. This is orthogonal to the existing lifecycle state machine (`active → archived → gutted → deleted`), which handles permanent transitions; pausing is a reversible operational toggle that does not belong in the state machine.

Extraction and embedding workers are unaffected — they continue processing already-queued tasks regardless of `scan_paused`.

## Schema

Migration in `init_db()` using the project's established `PRAGMA table_info` guard pattern (do **not** modify the `CREATE TABLE vaults` DDL in `executescript` — migration-only is sufficient and avoids divergence between new and upgraded DBs):

```python
cursor = conn.execute("PRAGMA table_info(vaults)")
vcols = [r['name'] for r in cursor.fetchall()]
if 'scan_paused' not in vcols:
    conn.execute("ALTER TABLE vaults ADD COLUMN scan_paused INTEGER NOT NULL DEFAULT 0")
```

This is idempotent and follows the same pattern used for all other column migrations in `init_db()`. Fresh DBs get `scan_paused = 0` by default; upgraded DBs get the same via the ALTER TABLE DEFAULT.

## Components

### `core/vault_manager.py`

New method `set_scan_paused(vault_id, paused: bool)`. Raises `VaultStateError` (the project-standard exception for vault operation failures) if `vault_id` is not found — consistent with how `transition()` handles unknown vaults:

```python
def set_scan_paused(self, vault_id: str, paused: bool) -> dict:
    vault = self.get_vault(vault_id)
    if not vault:
        raise VaultStateError(f"Vault {vault_id!r} not found")
    with self._connect() as conn:
        conn.execute(
            "UPDATE vaults SET scan_paused = ?, updated_at = ? WHERE vault_id = ?",
            (1 if paused else 0, self._now(), vault_id)
        )
        conn.commit()
    return self.get_vault(vault_id)
```

`list_vaults()` uses `SELECT *`, so `scan_paused` is returned automatically after migration. `get_vault()` likewise.

Calling `set_scan_paused` on a non-active vault (e.g. archived) is allowed and succeeds — `scan_paused=1` on an archived vault is a no-op since the ingestion worker already skips non-active vaults. No guard is needed.

### `api/routes/vaults.py`

New endpoint on the existing router (which already has `prefix="/api/vaults"`; use the relative path form to match all other routes in the file). Uses the module-level `_vm()` factory and `VaultStateError` → 404 pattern consistent with existing routes:

```python
class PauseScanRequest(BaseModel):
    paused: bool

@router.post("/{vault_id}/pause-scan")
def pause_vault_scan(vault_id: str, req: PauseScanRequest):
    try:
        vault = _vm().set_scan_paused(vault_id, req.paused)
        return vault
    except VaultStateError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

### `run.py`

In `ingestion_worker_run()`, the existing filter:

```python
active = [v for v in vaults if v['state'] == 'active']
```

becomes:

```python
active = [v for v in vaults if v['state'] == 'active' and not v.get('scan_paused')]
```

The `.get('scan_paused')` default of `None` (falsy) is safe for rows where the column is absent in an old schema.

### `frontend/vault.html`

**Vault card links row** — add `| PAUSE` / `| RESUME` after MANAGE:

- When `scan_paused = 0`: render `<a onclick="toggleVaultScanPause('...', true)">PAUSE</a>` in orange
- When `scan_paused = 1`: render `<a onclick="toggleVaultScanPause('...', false)">RESUME</a>` in green

**Vault card status badge** — when `scan_paused = 1`, render `SCAN PAUSED` in amber instead of `ACTIVE` in green. The vault remains in the `active` lifecycle state.

**JS function** — uses the existing `api()` helper (which prepends `/api` automatically) and calls `loadVaultCards()` (the existing vault card refresh function):

```javascript
async function toggleVaultScanPause(vaultId, paused) {
    await api(`/vaults/${vaultId}/pause-scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused })
    });
    loadVaultCards();
}
```

The `Content-Type: application/json` header is included explicitly since the `api()` helper does not set it by default.

## Interaction with Global Pause

The global `PAUSE` button (sets `settings.paused`) is independent. If both are active, scanning stops for two independent reasons. Resuming one does not affect the other.

## Error Handling

- `set_scan_paused` raises `VaultStateError` for unknown `vault_id`; route returns 404.
- Migration uses `PRAGMA table_info` guard — no error swallowing.
- `v.get('scan_paused')` returns `None` (falsy) if the column is missing from an old row — vault is scanned normally, safe default.

## Testing

`tests/test_vault_scan_pause.py` — 6 tests:

1. `test_scan_paused_column_exists` — `init_db()` adds `scan_paused` column with default 0
2. `test_set_scan_paused_true` — `set_scan_paused(vault_id, True)` sets column to 1, returns updated vault
3. `test_set_scan_paused_false` — `set_scan_paused(vault_id, False)` sets column back to 0
4. `test_set_scan_paused_unknown_vault` — raises `VaultStateError`
5. `test_ingestion_worker_skips_paused_vault` — mocked ingestor: paused vault (`scan_paused=1`) is not scanned, active unpauseed vault is scanned
6. `test_api_pause_scan_endpoint` — `POST /api/vaults/{id}/pause-scan` with `{"paused": true}` returns updated vault with `scan_paused=1`
