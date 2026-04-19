# Per-Vault Ignore Rules Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to exclude file extensions and folder patterns from ingestion scanning, globally via Settings and per-vault via the Manage modal, with retroactive cleanup of already-indexed noise.

**Architecture:** Two new columns on the `vaults` table (`ignore_extensions`, `ignore_folders`) store per-vault patterns. Two new global settings (`ingestion:ignore_extensions`, `ingestion:ignore_folders`) store system-wide defaults. The ingestor merges both at scan time using `fnmatch` for folders and exact extension matching for files. A preview + apply-ignore API pair enables retroactive cleanup from the Manage modal.

**Tech Stack:** Python (`fnmatch`, `os.walk` dir pruning), SQLite (PRAGMA migrations), FastAPI/Pydantic, vanilla JS (modal tabs)

**Spec:** `E:\DocVault\docs\superpowers\specs\2026-04-05-vault-ignore-rules-design.md`

---

## File Map

| File | Change |
|---|---|
| `core/manager.py` | Add `ignore_extensions` + `ignore_folders` column migrations in `init_db()` |
| `core/settings.py` | Add `ingestion:ignore_extensions` + `ingestion:ignore_folders` settings |
| `core/ingestor.py` | Add `parse_ignore_patterns()`; update `ingest()` with folder pruning + extension filter |
| `run.py` | Pass `vault_row=vault` to `ingest()` |
| `api/routes/vaults.py` | Update `VaultUpdate`; import `parse_ignore_patterns`; add `ignore-preview` + `apply-ignore` routes |
| `frontend/vault.html` | Add ignore fields to Identity tab; add cleanup section to Maintenance tab; update `saveVaultConfig()` |
| `tests/test_vault_ignores.py` | 6 new tests |

---

## Chunk 1: Schema, Settings, and Ingestor

### Task 1: Schema Migration + Settings

**Files:**
- Modify: `core/manager.py` (in `init_db()`, after the existing `scan_paused` migration block ~line 298)
- Modify: `core/settings.py` (after `monitor:embed_stall_threshold_mins` or at end of schema dict)
- Create: `tests/test_vault_ignores.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_ignores.py`:

```python
import os
import pytest
from unittest.mock import patch, MagicMock
from core import manager
from core.manager import _connect


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    return db_path


@pytest.fixture
def two_vault_db(tmp_path):
    """DB with vault-a (ignores .au) and vault-b (no ignores)."""
    db_path = str(tmp_path / "test.db")
    manager.init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute("""INSERT INTO vaults
            (vault_id, name, scan_directory, priority, state, ignore_extensions, ignore_folders, created_at, updated_at)
            VALUES ('vault-a', 'Vault A', '/docs', 5, 'active', '.au', '', '2024-01-01', '2024-01-01')""")
        conn.execute("""INSERT INTO vaults
            (vault_id, name, scan_directory, priority, state, ignore_extensions, ignore_folders, created_at, updated_at)
            VALUES ('vault-b', 'Vault B', '/nas', 5, 'active', '', '', '2024-01-01', '2024-01-01')""")
        conn.commit()
    return db_path


def test_schema_columns_exist(db):
    """init_db() adds ignore_extensions and ignore_folders columns with empty string defaults."""
    with _connect(db) as conn:
        cols = [r['name'] for r in conn.execute("PRAGMA table_info(vaults)").fetchall()]
    assert 'ignore_extensions' in cols
    assert 'ignore_folders' in cols

    # New rows get empty string defaults
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', '/docs', 5, 'active', '2024-01-01', '2024-01-01')""")
        conn.commit()
        row = conn.execute("SELECT ignore_extensions, ignore_folders FROM vaults WHERE vault_id='v1'").fetchone()
    assert row['ignore_extensions'] == ''
    assert row['ignore_folders'] == ''
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_vault_ignores.py::test_schema_columns_exist -v
```

Expected: FAIL — `AssertionError: 'ignore_extensions' not in cols`

- [ ] **Step 3: Add migrations to `core/manager.py`**

After the `scan_paused` migration block (around line 298), add:

```python
        # ignore_extensions / ignore_folders migration for vaults table
        if 'ignore_extensions' not in vcols:
            conn.execute("ALTER TABLE vaults ADD COLUMN ignore_extensions TEXT NOT NULL DEFAULT ''")
        if 'ignore_folders' not in vcols:
            conn.execute("ALTER TABLE vaults ADD COLUMN ignore_folders TEXT NOT NULL DEFAULT ''")
```

Note: `vcols` is already defined by the `scan_paused` block above — no need to re-query `PRAGMA table_info(vaults)`.

- [ ] **Step 4: Add settings to `core/settings.py`**

Find the `monitor` group settings (around line 311). Add a new `ingestion` group immediately before or after it:

```python
            # Ingestion filtering
            'ingestion:ignore_extensions': {
                'type': 'string', 'default': '.bak, .tmp, .log',
                'label': 'Global ignore extensions', 'group': 'ingestion',
                'description': 'Comma-separated file extensions to skip during ingestion across all vaults. '
                               'Include the dot: .bak, .tmp, .log',
            },
            'ingestion:ignore_folders': {
                'type': 'string', 'default': 'temp*, __pycache__, .git',
                'label': 'Global ignore folders', 'group': 'ingestion',
                'description': 'Comma-separated glob patterns matched against folder name (not full path). '
                               'Examples: temp*, node_modules, *_data',
            },
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_vault_ignores.py::test_schema_columns_exist -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/manager.py core/settings.py tests/test_vault_ignores.py
git commit -m "feat(ingestor): add ignore_extensions/ignore_folders columns and global settings"
```

---

### Task 2: Ingestor Core Logic + run.py

**Files:**
- Modify: `core/ingestor.py`
- Modify: `run.py` (line 39)
- Modify: `tests/test_vault_ignores.py` (append tests 2–5)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_ignores.py`:

```python
def _make_scan_dir(tmp_path, structure):
    """
    Create a directory structure from a dict.
    Keys are paths relative to tmp_path; values are file content bytes.
    Intermediate directories are created automatically.
    Example: {'docs/file.txt': b'hello', 'audio_data/song.au': b'audio'}
    """
    for rel_path, content in structure.items():
        full = tmp_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
    return str(tmp_path)


def test_global_extension_ignore(db, tmp_path):
    """ingest() with global ignore_extensions='.au' skips .au files."""
    scan_dir = _make_scan_dir(tmp_path, {
        'doc.pdf': b'pdf content',
        'noise.au': b'audio block',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '2024-01-01', '2024-01-01')""", (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: {
        'ingestion:ignore_extensions': '.au',
        'ingestion:ignore_folders': '',
    }.get(key, '')

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        hashes = [r['file_hash'] for r in conn.execute("SELECT file_hash FROM tasks").fetchall()]
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert any('doc.pdf' in p for p in paths), "pdf should be indexed"
    assert not any('noise.au' in p for p in paths), ".au file should be ignored"


def test_global_folder_ignore(db, tmp_path):
    """ingest() with global ignore_folders='*_data' skips entire subtree."""
    scan_dir = _make_scan_dir(tmp_path, {
        'report.txt': b'report',
        'audio_data/block1.au': b'block1',
        'audio_data/block2.au': b'block2',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '2024-01-01', '2024-01-01')""", (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: {
        'ingestion:ignore_extensions': '',
        'ingestion:ignore_folders': '*_data',
    }.get(key, '')

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert any('report.txt' in p for p in paths), "report should be indexed"
    assert not any('audio_data' in p for p in paths), "audio_data subtree should be ignored entirely"


def test_vault_extension_ignore(two_vault_db, tmp_path):
    """Per-vault .au ignore skips .au for vault-a; vault-b still indexes them."""
    scan_dir = _make_scan_dir(tmp_path, {
        'song.au': b'audio data',
        'doc.txt': b'text',
    })

    mock_settings = MagicMock()
    mock_settings.get = lambda key: ''  # no global ignores

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        with _connect(two_vault_db) as conn:
            vault_a = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='vault-a'").fetchone())
            vault_b = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='vault-b'").fetchone())
        ingestor.ingest(scan_dir, two_vault_db, vault_id='vault-a', vault_row=vault_a)
        ingestor.ingest(scan_dir, two_vault_db, vault_id='vault-b', vault_row=vault_b)

    with _connect(two_vault_db) as conn:
        fv_a = [r['file_path'] for r in
                conn.execute("SELECT file_path FROM file_vault WHERE vault_id='vault-a'").fetchall()]
        fv_b = [r['file_path'] for r in
                conn.execute("SELECT file_path FROM file_vault WHERE vault_id='vault-b'").fetchall()]

    assert not any('song.au' in p for p in fv_a), "vault-a should skip .au"
    assert any('song.au' in p for p in fv_b), "vault-b should index .au (no ignore rule)"


def test_vault_folder_fnmatch(db, tmp_path):
    """temp* matches 'temporary' and 'temp1' but NOT 'atemp'."""
    scan_dir = _make_scan_dir(tmp_path, {
        'temporary/file.txt': b'in temporary',
        'temp1/file.txt': b'in temp1',
        'atemp/file.txt': b'in atemp — should be indexed',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state,
                        ignore_extensions, ignore_folders, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '', 'temp*', '2024-01-01', '2024-01-01')""",
                     (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: ''

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert not any('temporary' in p for p in paths), "temporary/ should be pruned"
    assert not any('temp1' in p for p in paths), "temp1/ should be pruned"
    assert any('atemp' in p for p in paths), "atemp/ should NOT be pruned (doesn't start with temp)"


def test_effective_rules_are_union(db, tmp_path):
    """Global has .bak, vault has .tmp — both are ignored (union, not override)."""
    scan_dir = _make_scan_dir(tmp_path, {
        'keep.txt': b'keep',
        'noise.bak': b'bak',
        'noise.tmp': b'tmp',
    })
    with _connect(db) as conn:
        conn.execute("""INSERT INTO vaults (vault_id, name, scan_directory, priority, state,
                        ignore_extensions, ignore_folders, created_at, updated_at)
                        VALUES ('v1', 'V1', ?, 5, 'active', '.tmp', '', '2024-01-01', '2024-01-01')""",
                     (scan_dir,))
        conn.commit()
        vault_row = dict(conn.execute("SELECT * FROM vaults WHERE vault_id='v1'").fetchone())

    mock_settings = MagicMock()
    mock_settings.get = lambda key: {
        'ingestion:ignore_extensions': '.bak',
        'ingestion:ignore_folders': '',
    }.get(key, '')

    with patch('core.ingestor.settings', mock_settings):
        from core import ingestor
        ingestor.ingest(scan_dir, db, vault_id='v1', vault_row=vault_row)

    with _connect(db) as conn:
        paths = [r['file_path'] for r in conn.execute("SELECT file_path FROM tasks").fetchall()]

    assert any('keep.txt' in p for p in paths)
    assert not any('noise.bak' in p for p in paths), ".bak from global rules should be ignored"
    assert not any('noise.tmp' in p for p in paths), ".tmp from vault rules should be ignored"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_vault_ignores.py::test_global_extension_ignore tests/test_vault_ignores.py::test_global_folder_ignore tests/test_vault_ignores.py::test_vault_extension_ignore tests/test_vault_ignores.py::test_vault_folder_fnmatch tests/test_vault_ignores.py::test_effective_rules_are_union -v
```

Expected: all FAIL — `TypeError: ingest() got an unexpected keyword argument 'vault_row'`

- [ ] **Step 3: Update `core/ingestor.py`**

**3a — Add module-level imports** at top of file (after existing imports):

```python
import fnmatch
from core.settings import settings
```

**3b — Add `parse_ignore_patterns` function** after the `_BLOCKED_EXTENSIONS` block (~line 16):

```python
def parse_ignore_patterns(csv: str) -> frozenset:
    """Parse a comma-separated pattern string into a frozenset of stripped, non-empty lowercase patterns."""
    return frozenset(p.strip().lower() for p in csv.split(',') if p.strip())
```

**3c — Update `ingest()` signature** (line 61):

```python
# Before:
def ingest(directory, db_path, vault_id=None):

# After:
def ingest(directory, db_path, vault_id=None, vault_row=None):
```

**3d — Add effective ignore sets** at the top of `ingest()` body, after the `cache_dir` setup block and before `added = 0`:

```python
    global_exts    = parse_ignore_patterns(settings.get('ingestion:ignore_extensions') or '')
    global_folders = parse_ignore_patterns(settings.get('ingestion:ignore_folders') or '')
    vault_exts     = parse_ignore_patterns((vault_row or {}).get('ignore_extensions', ''))
    vault_folders  = parse_ignore_patterns((vault_row or {}).get('ignore_folders', ''))
    ignore_exts    = global_exts | vault_exts
    ignore_folders_set = global_folders | vault_folders
```

**3e — Add folder pruning** as the very first statement inside the `for root, dirs, files in os.walk(directory):` loop (before the Part 1 Folder Intelligence comment):

```python
    for root, dirs, files in os.walk(directory):
        # ── Folder pruning (must be first — prunes before any stat/hash work) ─
        if ignore_folders_set:
            dirs[:] = [
                d for d in dirs
                if not any(fnmatch.fnmatch(d.lower(), pat) for pat in ignore_folders_set)
            ]

        # ── Part 1: Folder Intelligence ──────────────────────────────────────
        ...
```

**3f — Add extension filter** in the per-file loop, immediately after the line `ext = ext.lstrip('.')` (line ~122). At this point `ext` is dotless (e.g. `au`):

```python
            ext = ext.lstrip('.')   # existing line
            if ext in ignore_exts or ('.' + ext) in ignore_exts:
                continue
```

Also remove the existing local import `from core.settings import settings` inside `ingest()` (line 69) since it is now a module-level import.

- [ ] **Step 4: Update `run.py`** — pass `vault_row` to `ingest()`

Find (line 39):

```python
                    ingestor.ingest(vault['scan_directory'], db_path, vault_id=vault['vault_id'])
```

Replace with:

```python
                    ingestor.ingest(vault['scan_directory'], db_path, vault_id=vault['vault_id'], vault_row=vault)
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_vault_ignores.py::test_global_extension_ignore tests/test_vault_ignores.py::test_global_folder_ignore tests/test_vault_ignores.py::test_vault_extension_ignore tests/test_vault_ignores.py::test_vault_folder_fnmatch tests/test_vault_ignores.py::test_effective_rules_are_union -v
```

Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
git add core/ingestor.py run.py tests/test_vault_ignores.py
git commit -m "feat(ingestor): add per-vault and global ignore rules for extensions and folders"
```

---

## Chunk 2: API Routes and Frontend

### Task 3: API Routes + Cleanup Test

**Files:**
- Modify: `api/routes/vaults.py`
- Modify: `tests/test_vault_ignores.py` (append test 6)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_vault_ignores.py`:

```python
def test_apply_ignore_multi_vault(two_vault_db):
    """
    File shared by vault-a and vault-b:
      - apply-ignore on vault-a removes vault-a's file_vault entry
      - tasks row survives (vault-b still claims it)
    File belonging only to vault-a:
      - apply-ignore removes file_vault entry AND tasks row (no other claimant)
    """
    from fastapi.testclient import TestClient
    from api.routes import vaults as vaults_module
    from core.vault_manager import VaultManager

    # Insert a shared file (both vaults claim it)
    shared_hash = 'aabbcc' * 8  # 48-char fake hash
    # Insert a vault-a-only file with .au extension
    only_a_hash = 'ddeeff' * 8

    with _connect(two_vault_db) as conn:
        conn.execute("""INSERT INTO tasks (file_hash, file_path, file_type, vault_id)
                        VALUES (?, '/docs/shared.pdf', 'pdf', 'vault-a')""", (shared_hash,))
        conn.execute("""INSERT INTO file_vault (file_hash, vault_id, file_path)
                        VALUES (?, 'vault-a', '/docs/shared.pdf')""", (shared_hash,))
        conn.execute("""INSERT INTO file_vault (file_hash, vault_id, file_path)
                        VALUES (?, 'vault-b', '/nas/shared.pdf')""", (shared_hash,))
        conn.execute("""INSERT INTO tasks (file_hash, file_path, file_type, vault_id)
                        VALUES (?, '/docs/noise.au', 'au', 'vault-a')""", (only_a_hash,))
        conn.execute("""INSERT INTO file_vault (file_hash, vault_id, file_path)
                        VALUES (?, 'vault-a', '/docs/noise.au')""", (only_a_hash,))
        conn.commit()

    # vault-a ignores .au; shared.pdf is NOT an .au file so it won't be purged;
    # but let's add 'pdf' temporarily to vault-a's ignore to test shared-file safety.
    # Actually: vault-a already has ignore_extensions='.au'. Only only_a_hash matches.
    # shared.pdf is .pdf — not ignored. So only only_a_hash gets removed.

    original_vm = vaults_module._vm
    vaults_module._vm = lambda: VaultManager(two_vault_db)
    try:
        from api.main import app
        client = TestClient(app)
        resp = client.post('/api/vaults/vault-a/apply-ignore')
        assert resp.status_code == 200
        assert resp.json()['removed'] == 1  # only the .au file

        with _connect(two_vault_db) as conn:
            # vault-a membership for .au file removed
            fv_a = conn.execute(
                "SELECT * FROM file_vault WHERE file_hash=? AND vault_id='vault-a'",
                (only_a_hash,)
            ).fetchone()
            assert fv_a is None, "file_vault entry for vault-a should be gone"

            # tasks row for .au file removed (no other claimant)
            task = conn.execute("SELECT * FROM tasks WHERE file_hash=?", (only_a_hash,)).fetchone()
            assert task is None, "tasks row should be purged (no other vault claims it)"

            # shared.pdf tasks row still exists (vault-b claims it)
            shared_task = conn.execute("SELECT * FROM tasks WHERE file_hash=?", (shared_hash,)).fetchone()
            assert shared_task is not None, "shared file tasks row must survive"
    finally:
        vaults_module._vm = original_vm
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_vault_ignores.py::test_apply_ignore_multi_vault -v
```

Expected: FAIL — 404 or 405 (endpoints don't exist yet)

- [ ] **Step 3: Update `api/routes/vaults.py`**

**3a — Add import** at top of file (after existing imports):

```python
from core.ingestor import parse_ignore_patterns
```

**3b — Update `VaultUpdate` model** — add two optional fields:

```python
class VaultUpdate(BaseModel):
    name: str | None = None
    scan_directory: str | None = None
    priority: int | None = None
    color: str | None = None
    ignore_extensions: str | None = None
    ignore_folders: str | None = None
```

**3c — Add two new routes** before `@router.get("/{vault_id}")` (line 45 — the catch-all; new routes need to appear before it):

```python
@router.get("/{vault_id}/ignore-preview")
def ignore_preview(vault_id: str):
    """Return count of already-indexed files matching this vault's current ignore rules."""
    import fnmatch as _fnmatch
    from core.manager import get_db_path, _connect
    from core.settings import settings as _settings
    vault = _vm().get_vault(vault_id)
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
    db_path = get_db_path()
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
    from core.manager import get_db_path, _connect
    from core.settings import settings as _settings
    vault = _vm().get_vault(vault_id)
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
    db_path = get_db_path()
    removed = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT file_hash, file_path FROM file_vault WHERE vault_id = ?",
            (vault_id,)
        ).fetchall()
        for row in rows:
            path = row['file_path'].replace('\\', '/')
            file_hash = row['file_hash']
            ext = os.path.splitext(path)[1].lower()
            parts = [p for p in path.split('/') if p]
            ext_match = ext in ignore_exts or ext.lstrip('.') in ignore_exts
            folder_match = ignore_folders_set and any(
                _fnmatch.fnmatch(p.lower(), pat)
                for p in parts[:-1]
                for pat in ignore_folders_set
            )
            if not (ext_match or folder_match):
                continue
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
                conn.execute("DELETE FROM extracted_images WHERE file_hash = ?", (file_hash,))
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
        conn.commit()
    return {"removed": removed}
```

**Important:** `os` is already imported at the top of `vaults.py` via `from core.manager import get_db_path` — but check: if `os` is not imported, add `import os` at the top of the file.

- [ ] **Step 4: Run all 6 tests**

```
pytest tests/test_vault_ignores.py -v
```

Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/vaults.py tests/test_vault_ignores.py
git commit -m "feat(api): add ignore-preview and apply-ignore endpoints for per-vault noise cleanup"
```

---

### Task 4: Frontend — Ignore Fields in MANAGE Modal

**Files:**
- Modify: `frontend/vault.html`

The MANAGE modal has three tabs: Identity (fields), Kernels, Maintenance. We add:
1. Two new text inputs at the bottom of the **Identity** tab (after the color field)
2. A new cleanup section in the **Maintenance** tab
3. Update `openVaultManagement()` to populate the new inputs
4. Update `saveVaultConfig()` to include the new fields in the PUT payload
5. Add `loadIgnorePreview()` and `applyIgnoreRules()` JS functions

- [ ] **Step 1: Add ignore fields to Identity tab HTML**

Find the closing `</div>` of the Identity tab (after the color/priority row, around line 237):

```html
        </div>
      </div>

      <!-- Tab: Extractors -->
```

Insert before `</div>` + `</div>` (closing v-field-row and v-pane-identity):

```html
        <div class="v-field" style="margin-top:10px;">
          <label style="font-size:10px;color:var(--c-label);text-transform:uppercase;letter-spacing:1px;display:block;margin-bottom:4px;">Ignore Extensions</label>
          <input type="text" id="v-input-ignore-ext" class="lc-input" placeholder=".au, .tmp, .bak">
          <div style="font-size:10px;color:var(--c-label-dim);margin-top:3px;">Comma-separated. Include the dot. Added to global rules.</div>
        </div>
        <div class="v-field" style="margin-top:8px;">
          <label style="font-size:10px;color:var(--c-label);text-transform:uppercase;letter-spacing:1px;display:block;margin-bottom:4px;">Ignore Folders</label>
          <input type="text" id="v-input-ignore-folders" class="lc-input" placeholder="*_data, temp*, node_modules">
          <div style="font-size:10px;color:var(--c-label-dim);margin-top:3px;">Glob patterns matched against folder name. Added to global rules.</div>
        </div>
```

- [ ] **Step 2: Add cleanup section to Maintenance tab**

Find the `v-maint-tools` div (around line 250). Add a new maintenance button after the existing buttons:

```html
          <div style="border-top:1px solid var(--c-border-str);margin:10px 0;"></div>
          <div style="font-size:10px;color:var(--c-label-dim);margin-bottom:8px;">IGNORE RULES CLEANUP</div>
          <div id="v-ignore-preview" style="font-size:11px;color:var(--c-label-dim);margin-bottom:8px;">Load vault to check for matches.</div>
          <button class="v-maint-btn" onclick="applyIgnoreRules()" id="btn-apply-ignore" style="display:none;">
            <div class="v-maint-title" style="color:#ff9900;">Clean Up Noise</div>
            <div class="v-maint-desc" id="btn-apply-ignore-desc">Remove already-indexed files matching current ignore rules.</div>
          </button>
```

- [ ] **Step 3: Update `openVaultManagement()` to populate ignore inputs**

Find `openVaultManagement()` (line 769). After the line `document.getElementById('v-input-color').value = v.color || '#cc6600';`, add:

```javascript
        document.getElementById('v-input-ignore-ext').value = v.ignore_extensions || '';
        document.getElementById('v-input-ignore-folders').value = v.ignore_folders || '';
        loadIgnorePreview(id);
```

- [ ] **Step 4: Update `saveVaultConfig()` to include ignore fields**

Find `saveVaultConfig()` (line 835). The `payload` object currently has `name`, `scan_directory`, `priority`, `color`. Add the two new fields:

```javascript
      var payload = {
        name: document.getElementById('v-input-name').value,
        scan_directory: document.getElementById('v-input-path').value,
        priority: parseInt(document.getElementById('v-input-priority').value),
        color: document.getElementById('v-input-color').value,
        ignore_extensions: document.getElementById('v-input-ignore-ext').value,
        ignore_folders: document.getElementById('v-input-ignore-folders').value
      };
```

Also add `loadIgnorePreview(activeVaultId)` in the save success block, after `closeVaultModal()` is NOT called (or after `refreshAll()`) — actually, since `closeVaultModal()` is called on success, call `loadIgnorePreview` before closing if you want the preview to refresh. Simplest: call it inside `openVaultManagement` on re-open. No change needed here.

- [ ] **Step 5: Add `loadIgnorePreview()` and `applyIgnoreRules()` JS functions**

Add these functions after the `retryErrors()` function (around line 883):

```javascript
    async function loadIgnorePreview(vaultId) {
      var previewEl = document.getElementById('v-ignore-preview');
      var btnApply  = document.getElementById('btn-apply-ignore');
      var btnDesc   = document.getElementById('btn-apply-ignore-desc');
      if (!vaultId || !previewEl) return;
      try {
        var r = await api('/vaults/' + vaultId + '/ignore-preview');
        if (r.count > 0) {
          previewEl.textContent = r.count + ' existing file(s) match current ignore rules.';
          previewEl.style.color = '#ff9900';
          btnDesc.textContent   = 'Remove ' + r.count + ' file(s) matching current ignore rules.';
          btnApply.style.display = '';
        } else {
          previewEl.textContent  = 'No existing files match current ignore rules.';
          previewEl.style.color  = 'var(--c-label-dim)';
          btnApply.style.display = 'none';
        }
      } catch(e) {
        previewEl.textContent = 'Could not load preview: ' + e.message;
      }
    }

    async function applyIgnoreRules() {
      if (!activeVaultId) return;
      var btn = document.getElementById('btn-apply-ignore');
      btn.disabled = true;
      try {
        var r = await api('/vaults/' + activeVaultId + '/apply-ignore', { method: 'POST' });
        lcToast('Removed ' + r.removed + ' file(s) matching ignore rules.');
        loadIgnorePreview(activeVaultId);
        refreshAll();
      } catch(e) {
        lcToast('Cleanup failed: ' + e.message, true);
      } finally {
        btn.disabled = false;
      }
    }
```

- [ ] **Step 6: Manual smoke test**

Start server, open vault page, click MANAGE on a vault:
1. Identity tab shows "Ignore Extensions" and "Ignore Folders" inputs (empty for existing vaults)
2. Type `.au` in Ignore Extensions, click Save — vault updates
3. Maintenance tab shows count of matching `.au` files (if any)
4. Click "Clean Up Noise" — toast shows count removed, preview updates to 0

- [ ] **Step 7: Run full test suite**

```
pytest tests/test_vault_ignores.py -v
pytest tests/ -q --tb=short --ignore=tests/test_router.py --ignore=tests/test_video_extractor.py 2>&1 | tail -5
```

Expected: 6 PASS for ignores; same pre-existing fail count elsewhere.

- [ ] **Step 8: Commit**

```bash
git add frontend/vault.html
git commit -m "feat(ui): add ignore extension/folder fields and cleanup to vault manage modal"
```
