# Per-Vault Scan Pause Design

## Goal

Allow individual vaults to have their ingestion scanning paused and resumed independently, without affecting other vaults or the global worker pause state.

## Background

DocVault's ingestion worker scans all `active` vaults on a 60-second cycle. The only existing pause mechanism is a global toggle (`settings.paused`) that halts all workers (extraction, embedding, and ingestion) for all vaults. There is no way to stop scanning a single vault — for example, a NAS vault that is temporarily unreachable — without pausing the entire system.

## Architecture

A `scan_paused` boolean column is added to the `vaults` table. The ingestion worker skips vaults where `scan_paused = 1`. This is orthogonal to the existing lifecycle state machine (`active → archived → gutted → deleted`), which handles permanent transitions; pausing is a reversible operational toggle that does not belong in the state machine.

Extraction and embedding workers are unaffected — they continue processing already-queued tasks regardless of `scan_paused`.

## Schema

Migration in `init_db()` (ALTER TABLE, idempotent via try/except):

```sql
ALTER TABLE vaults ADD COLUMN scan_paused INTEGER NOT NULL DEFAULT 0;
```

## Components

### `core/vault_manager.py`

New method `set_scan_paused(vault_id, paused: bool)`:

```python
def set_scan_paused(self, vault_id: str, paused: bool) -> dict:
    vault = self.get_vault(vault_id)
    if not vault:
        raise ValueError(f"Vault {vault_id!r} not found")
    with self._connect() as conn:
        conn.execute(
            "UPDATE vaults SET scan_paused = ?, updated_at = ? WHERE vault_id = ?",
            (1 if paused else 0, self._now(), vault_id)
        )
        conn.commit()
    return self.get_vault(vault_id)
```

`list_vaults()` already returns all columns via `SELECT *`, so `scan_paused` is included automatically after the migration.

### `api/routes/vaults.py`

New endpoint `POST /api/vaults/{vault_id}/pause-scan`:

```python
class PauseScanRequest(BaseModel):
    paused: bool

@router.post("/api/vaults/{vault_id}/pause-scan")
def pause_vault_scan(vault_id: str, req: PauseScanRequest):
    try:
        vm = VaultManager(get_db())
        vault = vm.set_scan_paused(vault_id, req.paused)
        return vault
    except ValueError as e:
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

### `frontend/vault.html`

**Vault card links row** — add `| PAUSE` / `| RESUME` after MANAGE:

- When `scan_paused = 0`: render `<a onclick="toggleVaultScanPause('...', true)">PAUSE</a>` in orange
- When `scan_paused = 1`: render `<a onclick="toggleVaultScanPause('...', false)">RESUME</a>` in green

**Vault card status badge** — when `scan_paused = 1`, render `SCAN PAUSED` in amber instead of `ACTIVE` in green. The vault remains in the `active` lifecycle state.

**JS function:**

```javascript
async function toggleVaultScanPause(vaultId, paused) {
    await api(`/api/vaults/${vaultId}/pause-scan`, {
        method: 'POST',
        body: JSON.stringify({ paused })
    });
    loadVaultStatus();  // existing refresh function
}
```

## Interaction with Global Pause

The global `PAUSE` button (sets `settings.paused`) is independent. If both are active, scanning stops for two independent reasons. Resuming one does not affect the other.

## Error Handling

- `set_scan_paused` raises `ValueError` for unknown vault_id; route returns 404.
- If the ALTER TABLE migration fails (e.g., column already exists from a previous run), the exception is caught and ignored — same pattern used for other migrations in `init_db()`.
- If `scan_paused` column is missing from an old DB row, `v.get('scan_paused')` returns `None` (falsy) — vault is scanned normally, safe default.

## Testing

`tests/test_vault_scan_pause.py` — 6 tests:

1. `test_scan_paused_column_exists` — `init_db()` adds `scan_paused` column with default 0
2. `test_set_scan_paused_true` — `set_scan_paused(vault_id, True)` sets column to 1
3. `test_set_scan_paused_false` — `set_scan_paused(vault_id, False)` sets column back to 0
4. `test_set_scan_paused_unknown_vault` — raises `ValueError`
5. `test_ingestion_worker_skips_paused_vault` — mocked ingestor: paused vault is not scanned, active vault is
6. `test_api_pause_scan_endpoint` — `POST /api/vaults/{id}/pause-scan` with `{"paused": true}` returns updated vault with `scan_paused=1`
