# Per-Vault Scan Pause Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PAUSE/RESUME link to each vault card so individual vault ingestion scanning can be paused without affecting other vaults or the global pause state.

**Architecture:** A `scan_paused` boolean column on the `vaults` table. The ingestion worker skips vaults where `scan_paused = 1`. A new `set_scan_paused()` method on `VaultManager` and a `POST /api/vaults/{id}/pause-scan` endpoint expose the toggle. The vault card UI shows an inline PAUSE/RESUME link and changes the badge to amber "SCAN PAUSED" when paused.

**Tech Stack:** Python/SQLite (migration via PRAGMA table_info guard), FastAPI/Pydantic (new endpoint), Vanilla JS (vault card rendering in `frontend/vault.html`)

**Spec:** `E:\DocVault\docs\superpowers\specs\2026-04-05-vault-scan-pause-design.md`

---

## File Map

| File | Change |
|---|---|
| `core/manager.py` | Add `scan_paused` column migration in `init_db()` |
| `core/vault_manager.py` | New method `set_scan_paused(vault_id, paused)` |
| `run.py` | Filter paused vaults in `ingestion_worker_run()` |
| `api/routes/vaults.py` | New `PauseScanRequest` model + `POST /{vault_id}/pause-scan` endpoint |
| `frontend/vault.html` | Update `loadVaultCards()`: badge + PAUSE/RESUME link + JS function |
| `tests/test_vault_scan_pause.py` | 6 new tests |

---

## Chunk 1: Backend — Schema, VaultManager, Worker

### Task 1: Schema Migration

**Files:**
- Modify: `core/manager.py` (in `init_db()`, after the `extracted_images` PRAGMA block around line 289)
- Create: `tests/test_vault_scan_pause.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_scan_pause.py`:

```python
import pytest
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def vault_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'Vault One', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


def test_scan_paused_column_exists(db):
    """init_db() adds scan_paused column with default 0."""
    with _connect(db) as conn:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()]
    assert 'scan_paused' in cols

    # Default value is 0 for new rows
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
        row = conn.execute("SELECT scan_paused FROM vaults WHERE vault_id='v1'").fetchone()
    assert row['scan_paused'] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_vault_scan_pause.py::test_scan_paused_column_exists -v
```

Expected: FAIL — `AssertionError: 'scan_paused' not in cols`

- [ ] **Step 3: Add migration to `core/manager.py`**

After the `extracted_images` PRAGMA block (around line 292) in `init_db()`, add:

```python
        # scan_paused migration for vaults table
        cursor = conn.execute("PRAGMA table_info(vaults)")
        vcols = [r['name'] for r in cursor.fetchall()]
        if 'scan_paused' not in vcols:
            conn.execute("ALTER TABLE vaults ADD COLUMN scan_paused INTEGER NOT NULL DEFAULT 0")
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_vault_scan_pause.py::test_scan_paused_column_exists -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/manager.py tests/test_vault_scan_pause.py
git commit -m "feat(vault): add scan_paused column migration + test"
```

---

### Task 2: VaultManager.set_scan_paused + Tests 2-4

**Files:**
- Modify: `core/vault_manager.py` (add `set_scan_paused` method)
- Modify: `tests/test_vault_scan_pause.py` (add tests 2-4)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_scan_pause.py`:

```python
from core.vault_manager import VaultManager, VaultStateError


def test_set_scan_paused_true(vault_db):
    """set_scan_paused(vault_id, True) sets column to 1 and returns updated vault."""
    vm = VaultManager(vault_db)
    result = vm.set_scan_paused('v1', True)
    assert result['scan_paused'] == 1
    # Verify persisted
    vault = vm.get_vault('v1')
    assert vault['scan_paused'] == 1


def test_set_scan_paused_false(vault_db):
    """set_scan_paused(vault_id, False) sets column back to 0."""
    vm = VaultManager(vault_db)
    vm.set_scan_paused('v1', True)
    result = vm.set_scan_paused('v1', False)
    assert result['scan_paused'] == 0


def test_set_scan_paused_unknown_vault(vault_db):
    """set_scan_paused raises VaultStateError for unknown vault_id."""
    vm = VaultManager(vault_db)
    with pytest.raises(VaultStateError):
        vm.set_scan_paused('no-such-vault', True)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_vault_scan_pause.py::test_set_scan_paused_true tests/test_vault_scan_pause.py::test_set_scan_paused_false tests/test_vault_scan_pause.py::test_set_scan_paused_unknown_vault -v
```

Expected: FAIL — `AttributeError: 'VaultManager' object has no attribute 'set_scan_paused'`

- [ ] **Step 3: Add `set_scan_paused` to `core/vault_manager.py`**

Add this method to the `VaultManager` class (after the `restore` method or before the extractor methods):

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

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_vault_scan_pause.py::test_set_scan_paused_true tests/test_vault_scan_pause.py::test_set_scan_paused_false tests/test_vault_scan_pause.py::test_set_scan_paused_unknown_vault -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add core/vault_manager.py tests/test_vault_scan_pause.py
git commit -m "feat(vault): add VaultManager.set_scan_paused method"
```

---

### Task 3: Ingestion Worker Filter + API Endpoint + Tests 5-6

**Files:**
- Modify: `run.py` (filter paused vaults in `ingestion_worker_run`)
- Modify: `api/routes/vaults.py` (new endpoint)
- Modify: `tests/test_vault_scan_pause.py` (add tests 5-6)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_scan_pause.py`:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


def test_ingestion_worker_skips_paused_vault(vault_db):
    """Paused vault (scan_paused=1) is not passed to ingestor; active vault is."""
    with _connect(vault_db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, scan_paused, created_at, updated_at)
                        VALUES ('v2', 'Vault Two', '/other', 5, 'active', 1, '2024-01-01', '2024-01-01')""")
        conn.commit()

    scanned = []

    def fake_ingest(scan_dir, db_path, vault_id):
        scanned.append(vault_id)

    with patch('core.ingestor.ingest', side_effect=fake_ingest):
        from core.vault_manager import VaultManager
        vm = VaultManager(vault_db)
        vaults = vm.list_vaults()
        active = [v for v in vaults if v['state'] == 'active' and not v.get('scan_paused')]
        import core.ingestor as ingestor_mod
        for v in active:
            ingestor_mod.ingest(v['scan_directory'], vault_db, vault_id=v['vault_id'])

    assert 'v1' in scanned       # active, not paused → scanned
    assert 'v2' not in scanned   # active, but paused → skipped


def test_api_pause_scan_endpoint(vault_db):
    """POST /api/vaults/{id}/pause-scan with {paused: true} returns vault with scan_paused=1."""
    import os, sys
    # Ensure the app can find the DB
    os.environ['DOCVAULT_DB'] = vault_db

    from api.routes import vaults as vaults_module
    # Patch _vm() to use our test DB
    original_vm = vaults_module._vm
    vaults_module._vm = lambda: VaultManager(vault_db)

    try:
        from api.main import app
        client = TestClient(app)
        response = client.post('/api/vaults/v1/pause-scan',
                               json={'paused': True})
        assert response.status_code == 200
        data = response.json()
        assert data['scan_paused'] == 1
    finally:
        vaults_module._vm = original_vm
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_vault_scan_pause.py::test_ingestion_worker_skips_paused_vault tests/test_vault_scan_pause.py::test_api_pause_scan_endpoint -v
```

Expected: FAIL — worker test may pass (logic inline), API test fails with 404 (endpoint missing)

- [ ] **Step 3: Update ingestion worker filter in `run.py`**

Find the line (around line 34 in the ingestion worker function):

```python
active = [v for v in vaults if v['state'] == 'active']
```

Change to:

```python
active = [v for v in vaults if v['state'] == 'active' and not v.get('scan_paused')]
```

- [ ] **Step 4: Add endpoint to `api/routes/vaults.py`**

Add after the existing imports and models, before the routes (or after `class VaultSettingSet`):

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

**Important:** Add this route BEFORE the `/{vault_id}/settings` and `/{vault_id}/extractors` parameterised sub-routes — specific routes before parameterised routes is the project rule. Concretely, place `PauseScanRequest` after `VaultSettingSet` (line 26) and the route anywhere before `@router.get("/{vault_id}/settings")`.

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_vault_scan_pause.py -v
```

Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add run.py api/routes/vaults.py tests/test_vault_scan_pause.py
git commit -m "feat(vault): ingestion worker skips scan_paused vaults; add pause-scan API endpoint"
```

---

## Chunk 2: Frontend — Vault Card UI

### Task 4: Vault Card PAUSE/RESUME Link + Badge

**Files:**
- Modify: `frontend/vault.html` (update `loadVaultCards()`, add `toggleVaultScanPause()`)

- [ ] **Step 1: Locate `loadVaultCards` in `frontend/vault.html`**

Find the vault card rendering block (around line 416-435). The current card renders:

```javascript
'<a href="..." onclick="setFilter(...)...">VIEW FILES</a>' +
'<span ...>|</span>' +
'<a href="..." onclick="openVaultManagement(...)...">MANAGE</a>'
```

The `stateBadge` block (lines 419-421) renders either `lc-badge-archived` or `lc-badge-active`.

- [ ] **Step 2: Update `loadVaultCards` — badge logic**

Replace the `stateBadge` assignment block:

```javascript
var stateBadge = isArchived
  ? '<span class="lc-badge lc-badge-archived">' + v.state + '</span>'
  : '<span class="lc-badge lc-badge-active">' + v.state + '</span>';
```

With:

```javascript
var stateBadge = isArchived
  ? '<span class="lc-badge lc-badge-archived">' + v.state + '</span>'
  : v.scan_paused
    ? '<span class="lc-badge" style="background:#6b4800;color:#ffb347;">SCAN PAUSED</span>'
    : '<span class="lc-badge lc-badge-active">' + v.state + '</span>';
```

- [ ] **Step 3: Update `loadVaultCards` — PAUSE/RESUME link**

The links row is the last `<div>` inside the card builder (lines ~429-433 in vault.html). Replace the entire links `<div>` string — from the opening `'<div style="display:flex;gap:10px...'` through the closing `'</div>' +` (note: the closing `+` continues the outer card string concatenation; keep it).

**Old string** (exact match for the Edit tool):

```javascript
              '<div style="display:flex;gap:10px;font-size:11px;margin-top:4px;">' +
                '<a href="javascript:void(0)" onclick="setFilter(\'ALL\');setVaultFilter(\'' + v.vault_id + '\');scrollToLog();" style="color:var(--c-accent);font-weight:bold;text-transform:uppercase;letter-spacing:1px;">' + t('vault.card.view_files') + '</a>' +
                '<span style="color:var(--c-border-str);">|</span>' +
                '<a href="javascript:void(0)" onclick="openVaultManagement(\'' + v.vault_id + '\',\'' + escapeAttr(v.name) + '\')" style="color:var(--c-error);font-weight:bold;text-transform:uppercase;letter-spacing:1px;">' + t('vault.card.manage') + '</a>' +
              '</div>' +
```

**New string** (adds pause/resume link for active, non-archived vaults):

```javascript
              '<div style="display:flex;gap:10px;font-size:11px;margin-top:4px;">' +
                '<a href="javascript:void(0)" onclick="setFilter(\'ALL\');setVaultFilter(\'' + v.vault_id + '\');scrollToLog();" style="color:var(--c-accent);font-weight:bold;text-transform:uppercase;letter-spacing:1px;">' + t('vault.card.view_files') + '</a>' +
                '<span style="color:var(--c-border-str);">|</span>' +
                '<a href="javascript:void(0)" onclick="openVaultManagement(\'' + v.vault_id + '\',\'' + escapeAttr(v.name) + '\')" style="color:var(--c-error);font-weight:bold;text-transform:uppercase;letter-spacing:1px;">' + t('vault.card.manage') + '</a>' +
                (!isArchived ? (
                  '<span style="color:var(--c-border-str);">|</span>' +
                  '<a href="javascript:void(0)" onclick="toggleVaultScanPause(\'' + v.vault_id + '\',' + (v.scan_paused ? 'false' : 'true') + ')" style="color:' + (v.scan_paused ? '#4cff4c' : '#ff9900') + ';font-weight:bold;text-transform:uppercase;letter-spacing:1px;">' + (v.scan_paused ? 'RESUME' : 'PAUSE') + '</a>'
                ) : '') +
              '</div>' +
```

The trailing `+` on `'</div>' +` must remain — it continues the outer card string that ends with `'</div>'` (closing the outer card div) and then `.join('')`.

- [ ] **Step 4: Add `toggleVaultScanPause` JS function**

Add this function after the `resetStuck` function (around line 480) or alongside the other worker control functions:

```javascript
async function toggleVaultScanPause(vaultId, paused) {
    await api('/vaults/' + vaultId + '/pause-scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paused: paused })
    });
    loadVaultCards();
}
```

- [ ] **Step 5: Manual smoke test**

Start the server and open the vault page. Verify:
1. Each active vault card shows `| PAUSE` in orange after MANAGE
2. Clicking PAUSE changes badge to amber "SCAN PAUSED" and link to green "RESUME"
3. Clicking RESUME restores badge to "ACTIVE" and link to orange "PAUSE"
4. Archived/gutted vaults show no PAUSE/RESUME link

- [ ] **Step 6: Commit**

```bash
git add frontend/vault.html
git commit -m "feat(vault): add PAUSE/RESUME scan control to vault card UI"
```

---

## Final Verification

- [ ] **Run full test suite**

```
pytest tests/test_vault_scan_pause.py -v
```

Expected: 6 PASS, 0 FAIL

- [ ] **Run existing vault tests to confirm no regressions**

```
pytest tests/ -v --tb=short -q
```

Expected: All previously passing tests still pass.
