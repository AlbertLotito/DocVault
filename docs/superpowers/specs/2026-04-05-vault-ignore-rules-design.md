# Per-Vault Ignore Rules Design

## Goal

Allow users to exclude specific file extensions and folder patterns from ingestion scanning, both globally (via Settings) and per-vault (via the Vault Management modal). Includes retroactive cleanup of already-indexed files that match the rules.

## Background

The ingestor currently has two hardcoded global blocklists in `core/ingestor.py`:

- `_BLOCKLIST` — specific filenames always skipped (e.g. `desktop.ini`)
- `_BLOCKED_EXTENSIONS` — extensions always skipped (currently only `.nfo`)

There is no way to add new global exclusions without editing code, and no per-vault filtering at all. This causes noise when a vault's scan directory contains transient files (`.tmp`, `.bak`) or application-internal directories (Audacity `*_data` folders).

## Architecture

Two storage layers — global settings and per-vault columns — are merged at scan time into effective ignore sets. The ingestor applies them at walk time, pruning folder subtrees and skipping files before hashing. A preview + cleanup API lets users retroactively purge already-indexed noise. All changes are additive: vault rules add to global rules, never replace them.

## Storage

### Global (settings.db)

Two new entries in `core/settings.py` schema, `ingestion` group:

```python
'ingestion:ignore_extensions': {
    'type': 'string', 'default': '.bak, .tmp, .log',
    'label': 'Global ignore extensions', 'group': 'ingestion',
    'description': 'Comma-separated file extensions to skip during ingestion across all vaults. '
                   'Include the dot: .bak, .tmp, .log',
},
'ingestion:ignore_folders': {
    'type': 'string', 'default': 'temp*, __pycache__, .git',
    'label': 'Global ignore folders', 'group': 'ingestion',
    'description': 'Comma-separated folder name patterns (glob) to skip during ingestion across all vaults. '
                   'Matched against folder name only, not full path. Example: temp*, node_modules, *_data',
},
```

Both appear in the Settings UI automatically. The `ingestion` group is new; the settings page renders all groups so it will appear without additional UI changes.

### Per-Vault (vaults table)

Two new TEXT columns on the `vaults` table, added via the project's `PRAGMA table_info` migration guard in `init_db()`:

```python
cursor = conn.execute("PRAGMA table_info(vaults)")
vcols = [r['name'] for r in cursor.fetchall()]
if 'ignore_extensions' not in vcols:
    conn.execute("ALTER TABLE vaults ADD COLUMN ignore_extensions TEXT NOT NULL DEFAULT ''")
if 'ignore_folders' not in vcols:
    conn.execute("ALTER TABLE vaults ADD COLUMN ignore_folders TEXT NOT NULL DEFAULT ''")
```

Stored separately from `vault_settings` because the semantics are additive (union with global), not override. `list_vaults()` and `get_vault()` both use `SELECT *` so the new columns are returned automatically.

## Components

### `core/ingestor.py`

A module-level helper parses comma-separated patterns into a set. Named without a leading underscore so it can be cleanly imported by `api/routes/vaults.py`:

```python
def parse_ignore_patterns(csv: str) -> frozenset:
    """Parse a comma-separated pattern string into a frozenset of stripped, non-empty patterns."""
    return frozenset(p.strip().lower() for p in csv.split(',') if p.strip())
```

At the top of `ingest(directory, db_path, vault_id, vault_row)`:

```python
import fnmatch
from core.settings import settings

global_exts    = parse_ignore_patterns(settings.get('ingestion:ignore_extensions') or '')
global_folders = parse_ignore_patterns(settings.get('ingestion:ignore_folders') or '')
vault_exts     = parse_ignore_patterns((vault_row or {}).get('ignore_extensions', ''))
vault_folders  = parse_ignore_patterns((vault_row or {}).get('ignore_folders', ''))

ignore_exts    = global_exts | vault_exts
ignore_folders = global_folders | vault_folders
```

**`vault_row`** is a new parameter — the vault dict returned by `VaultManager.get_vault(vault_id)`. Callers in `run.py` already have the vault dict from `list_vaults()` and pass it through.

**Folder pruning** — placed at the **very top** of the `os.walk` loop body, before Part 1 (Folder Intelligence) and before the cache-dir guard. This ensures no stat calls or extractor checks are made for ignored subtrees:

```python
for root, dirs, files in os.walk(directory):
    # ── Folder pruning (must be first) ───────────────────────────────────
    dirs[:] = [
        d for d in dirs
        if not any(fnmatch.fnmatch(d.lower(), pat) for pat in ignore_folders)
    ]

    # ── Part 1: Folder Intelligence ──────────────────────────────────────
    ...
```

Mutating `dirs` in-place causes `os.walk` to skip the subtree entirely — no file hashing or stat calls for ignored directories.

**Extension filtering** — placed in the per-file loop **after** the line `ext = ext.lstrip('.')` (line 122 of the current file). At that point `ext` is dotless (e.g. `bak`), so both user input forms are covered:

```python
ext = ext.lstrip('.')   # existing line — filter goes after this
if ext in ignore_exts or ('.' + ext) in ignore_exts:
    continue
```

`bak` matches the dotless form; `.bak` matches the reconstructed dotted form. Placing the filter before the lstrip would cause `'.' + ext` to produce `..bak` (double dot) — placing it after avoids this.

### `core/vault_manager.py`

`update_vault()` already accepts `**kwargs` and builds a dynamic SET clause. No changes needed — `ignore_extensions` and `ignore_folders` flow through automatically once the columns exist.

### `api/routes/vaults.py`

`VaultUpdate` model gains two optional fields:

```python
class VaultUpdate(BaseModel):
    name: str | None = None
    scan_directory: str | None = None
    priority: int | None = None
    color: str | None = None
    ignore_extensions: str | None = None
    ignore_folders: str | None = None
```

Two new routes. Note: `/{vault_id}/ignore-preview` and `/{vault_id}/apply-ignore` do not conflict with `/{vault_id}/settings` — the extra path segment makes them unambiguous to FastAPI. Place them anywhere before the `GET /{vault_id}` catch-all (line 45), which is the actual constraint:

```python
@router.get("/{vault_id}/ignore-preview")
def ignore_preview(vault_id: str):
    """Return count of already-indexed files that match this vault's current ignore rules."""
    from core.manager import get_db_path, _connect
    from core.settings import settings
    import fnmatch, os
    vault = _vm().get_vault(vault_id)
    if not vault:
        raise HTTPException(404, "Vault not found")
    global_exts    = parse_ignore_patterns(settings.get('ingestion:ignore_extensions') or '')
    vault_exts     = parse_ignore_patterns(vault.get('ignore_extensions', ''))
    global_folders = parse_ignore_patterns(settings.get('ingestion:ignore_folders') or '')
    vault_folders  = parse_ignore_patterns(vault.get('ignore_folders', ''))
    ignore_exts    = global_exts | vault_exts
    ignore_folders_set = global_folders | vault_folders
    db_path = get_db_path()
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT fv.file_hash, fv.file_path FROM file_vault fv WHERE fv.vault_id = ?",
            (vault_id,)
        ).fetchall()
    count = 0
    for row in rows:
        path = row['file_path']
        ext = os.path.splitext(path)[1].lower()
        parts = path.replace('\\', '/').split('/')
        if ext in ignore_exts or ext.lstrip('.') in ignore_exts:
            count += 1
        elif any(any(fnmatch.fnmatch(p.lower(), pat) for pat in ignore_folders_set) for p in parts):
            count += 1
    return {"count": count}


@router.post("/{vault_id}/apply-ignore")
def apply_ignore(vault_id: str):
    """Remove already-indexed files matching the vault's current ignore rules.
    Removes file_vault membership. If no other vault claims the file, also removes
    tasks, FTS index entries, extracted_images, and Qdrant vectors.
    """
    from core.manager import get_db_path, _connect
    from core.settings import settings
    import fnmatch, os
    vault = _vm().get_vault(vault_id)
    if not vault:
        raise HTTPException(404, "Vault not found")
    # Build effective ignore sets (same logic as preview)
    global_exts    = parse_ignore_patterns(settings.get('ingestion:ignore_extensions') or '')
    vault_exts     = parse_ignore_patterns(vault.get('ignore_extensions', ''))
    global_folders = parse_ignore_patterns(settings.get('ingestion:ignore_folders') or '')
    vault_folders  = parse_ignore_patterns(vault.get('ignore_folders', ''))
    ignore_exts    = global_exts | vault_exts
    ignore_folders_set = global_folders | vault_folders
    db_path = get_db_path()
    removed = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT fv.file_hash, fv.file_path FROM file_vault fv WHERE fv.vault_id = ?",
            (vault_id,)
        ).fetchall()
        for row in rows:
            path = row['file_path']
            file_hash = row['file_hash']
            ext = os.path.splitext(path)[1].lower()
            parts = path.replace('\\', '/').split('/')
            matches = (
                ext in ignore_exts or ext.lstrip('.') in ignore_exts
                or any(any(fnmatch.fnmatch(p.lower(), pat) for pat in ignore_folders_set) for p in parts)
            )
            if not matches:
                continue
            # Remove vault membership
            conn.execute(
                "DELETE FROM file_vault WHERE file_hash = ? AND vault_id = ?",
                (file_hash, vault_id)
            )
            # If no other vault claims this file, purge globally
            other = conn.execute(
                "SELECT 1 FROM file_vault WHERE file_hash = ? LIMIT 1", (file_hash,)
            ).fetchone()
            if other is None:
                conn.execute("DELETE FROM tasks WHERE file_hash = ?", (file_hash,))
                conn.execute("DELETE FROM fts_index WHERE file_hash = ?", (file_hash,))
                conn.execute("DELETE FROM extracted_images WHERE file_hash = ?", (file_hash,))
                # Qdrant purge (best-effort)
                try:
                    from embeddings.vector_store import VectorStore
                    vs = VectorStore(
                        host=settings.get('qdrant:host'),
                        port=int(settings.get('qdrant:port')),
                        collection='docvault',
                    )
                    vs.delete(file_hash)
                except Exception:
                    pass
            removed += 1
        conn.commit()
    return {"removed": removed}
```

`parse_ignore_patterns` is defined in `core/ingestor.py` and imported in `api/routes/vaults.py` to avoid duplication:

```python
from core.ingestor import parse_ignore_patterns
```

### `run.py`

`ingestion_worker_run()` calls `ingest()` inside a `_scan_vault(vault)` inner function. Pass the vault dict through:

```python
# Before:
ingestor.ingest(vault['scan_directory'], db_path, vault_id=vault['vault_id'])

# After:
ingestor.ingest(vault['scan_directory'], db_path, vault_id=vault['vault_id'], vault_row=vault)
```

The loop variable is `vault` (not `v`) — match the existing code.

### `frontend/vault.html` — MANAGE Modal

The manage modal (opened by `openVaultManagement()`) gains two text inputs below the existing vault fields:

```html
<div class="lc-field-group">
  <label class="lc-label">IGNORE EXTENSIONS</label>
  <input id="mgmt-ignore-ext" class="lc-input" type="text"
         placeholder=".au, .tmp, .bak" />
  <div class="lc-field-hint">Comma-separated. Include the dot. Added to global rules.</div>
</div>
<div class="lc-field-group">
  <label class="lc-label">IGNORE FOLDERS</label>
  <input id="mgmt-ignore-folders" class="lc-input" type="text"
         placeholder="*_data, temp*, node_modules" />
  <div class="lc-field-hint">Glob patterns matched against folder name. Added to global rules.</div>
</div>
```

After saving, the modal calls `GET /api/vaults/{id}/ignore-preview` and shows:

```
47 existing files match current ignore rules.  [CLEAN UP NOW]
```

Clicking CLEAN UP NOW calls `POST /api/vaults/{id}/apply-ignore` and updates the display to `0 files match`.

## Existing Tables Used by apply-ignore

Both `file_vault` and `fts_index` are existing tables — no migration needed:

- `file_vault (file_hash, vault_id, file_path, added_at)` — junction table added in the multi-vault membership feature; `DELETE FROM file_vault WHERE file_hash = ? AND vault_id = ?` is the vault membership removal.
- `fts_index` — FTS5 virtual table: `CREATE VIRTUAL TABLE IF NOT EXISTS fts_index USING fts5(...)` in `init_db()`. Deleted by `file_hash` when purging globally.
- `extracted_images` — existing table, deleted by `file_hash`.

## Interaction with Existing Global Blocklists

The hardcoded `_BLOCKLIST` and `_BLOCKED_EXTENSIONS` in `core/ingestor.py` remain unchanged — they are a safety net for system files. The new settings-based rules are applied in addition to them.

## Error Handling

- `parse_ignore_patterns` returns an empty frozenset for empty/None input — no errors on missing vault rows or empty settings.
- `vault_row=None` is handled defensively: `(vault_row or {}).get(...)` returns `''` safely.
- Qdrant delete in `apply-ignore` is best-effort — wrapped in `try/except`. A Qdrant failure does not roll back the SQLite purge; vectors will be orphaned but harmless (they will not match searches since the file_hash no longer exists in tasks).
- `conn.commit()` is called once after all deletes — atomic batch.

## Testing

`tests/test_vault_ignores.py` — 6 tests:

1. `test_global_extension_ignore` — ingest with `ingestion:ignore_extensions = '.au'`; `.au` files are not added to tasks
2. `test_global_folder_ignore` — ingest with `ingestion:ignore_folders = '*_data'`; contents of `audio_data/` subtree are skipped entirely
3. `test_vault_extension_ignore` — per-vault `.au` ignore skips `.au` files for vault-A; vault-B scanning the same directory still indexes them (isolation)
4. `test_vault_folder_fnmatch` — `temp*` matches `temporary` and `temp1` but not `atemp`
5. `test_effective_rules_are_union` — global has `.bak`, vault has `.tmp`; both are ignored during ingest
6. `test_apply_ignore_multi_vault` — file shared by two vaults: after apply-ignore on vault-A, `file_vault` entry for vault-A is removed but task row survives (vault-B still claims it); file with only vault-A membership is fully purged
