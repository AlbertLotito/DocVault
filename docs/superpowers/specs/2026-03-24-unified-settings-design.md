# Unified Settings — Design Notes

**Date:** 2026-03-24
**Status:** Parked — future work. Requires dedicated dev branch before merging to master.

---

## Problem Statement

Currently, extractor kernels that require user configuration (API keys, file paths, thresholds, etc.) have their settings hand-declared in `core/settings.py`. This works for built-in extractors but breaks down for third-party or user-installed kernels:

- The user must manually edit `config.ini` or know the exact key names
- No labels, descriptions, or type information is surfaced in the UI
- There is no machine-readable contract between a kernel and the settings system

**Goal:** A kernel drops into the `extractors/` folder, passes certification, and its configuration needs automatically appear in the Settings UI — with correct labels, types, descriptions, and defaults. No manual editing of any system file required.

---

## User Experience (Target State)

1. User downloads `foo_extractor.py` and places it in `extractors/`
2. On next startup, the registry certifies it
3. User opens Settings → a new "foo" accordion group appears with all of Foo's settings
4. User fills in values and saves — done

If Foo is removed:
- Its settings are hidden from the UI immediately
- User is prompted once: "Foo extractor was removed. It stored 3 settings. Delete them?"
- User can choose to delete or keep (kept values are soft-deleted — hidden but recoverable if Foo is reinstalled)

If Foo is upgraded:
- New keys appear with their defaults, user is notified
- Removed keys go through the same removal flow as above
- Existing keys with user-stored values are preserved; label/description/default are updated from the new schema

---

## Kernel Contract

Each extractor module that requires configuration declares a `SETTINGS` dict alongside its existing `MANIFEST`:

```python
MANIFEST = {
    "id": "com.example.foo",
    "version": "1.2.0",          # MUST be semantic: MAJOR.MINOR.PATCH
    "name": "Foo Extractor",
    "extensions": ["foo"],
    "requires": ["some-lib"],
}

SETTINGS = {
    "foo:api_key": {
        "type": "string",
        "default": "",
        "label": "Foo API Key",
        "description": "API key from https://example.com/account. Required for extraction.",
        "secret": True,           # hint to UI: render as password field
    },
    "foo:timeout": {
        "type": "int",
        "default": 30,
        "label": "Request timeout (s)",
        "description": "Seconds before a Foo API call is abandoned.",
    },
}
```

### Version Format Enforcement

`version` in `MANIFEST` must match `^\d+\.\d+\.\d+$`. The registry rejects kernels with non-conforming version strings at certification time. This is required so versions can be compared reliably during upgrade detection.

### Key Naming Convention

Keys must be namespaced with the kernel's short name as a prefix (e.g. `foo:api_key`, not `api_key`). The registry enforces this: the prefix must match the `id` field's last component (`com.example.foo` → prefix must be `foo:`).

---

## Storage

### New DB Table: `kernel_settings`

Added to `settings.db`:

```sql
CREATE TABLE kernel_settings (
    key          TEXT PRIMARY KEY,
    kernel_id    TEXT NOT NULL,
    version      TEXT NOT NULL,    -- version of kernel that declared this key
    schema_json  TEXT NOT NULL,    -- JSON-serialised schema entry (type, default, label, etc.)
    orphaned     INTEGER DEFAULT 0 -- 1 = kernel removed, pending cleanup decision
);
```

At startup, the registry merges all non-orphaned `kernel_settings` rows into the live in-memory settings schema. This makes kernel settings indistinguishable from built-in settings at runtime.

User-stored values continue to live in the existing `settings` table (key → value). No change there.

### Master Schema (`core/settings.py`)

Two migration strategies were discussed:

**Option A — Full migration (preferred long-term):**
All kernel-specific settings are moved out of `core/settings.py` into each extractor's `SETTINGS` block. The master schema retains only truly global settings (`server:*`, `llm:*`, `monitor:*`, `paths:*`, `ui:*`). This is the clean end state but requires touching every built-in extractor.

**Option B — Coexistence rule (lower risk, migration path):**
Master schema entries take precedence. Kernel `SETTINGS` keys only apply if the key is not already in the master schema. Existing built-in extractors need no changes. New and third-party extractors use the `SETTINGS` contract. Technical debt remains in the master schema until a future migration cleans it up.

**Recommendation:** Start with Option B to ship the feature safely. Schedule Option A as a follow-on migration once the infrastructure is stable.

---

## Registry Lifecycle

### On Certification (new kernel)

1. Parse `SETTINGS` block (if present)
2. Enforce key naming convention and version format
3. Insert rows into `kernel_settings` for each key (or update if key already exists from a prior version)
4. Log new keys to `worker_log`

### On Version Bump (kernel upgraded)

Diff old `SETTINGS` (from `kernel_settings` table) against new `SETTINGS` (from module):

| Situation | Action |
|---|---|
| Key in new, not in old | Insert new row, notify user ("N new settings available") |
| Key in both, schema changed | Update `schema_json` and `version`, preserve user's stored value |
| Key in old, not in new | Mark `orphaned = 1`, hide from UI, prompt user for cleanup |

Version comparison uses semantic version ordering (`1.2.0 > 1.1.3`).

### On Removal / Decertification

1. Mark all of the kernel's `kernel_settings` rows as `orphaned = 1`
2. Keys are immediately hidden from the Settings UI
3. On next settings page load, show a one-time cleanup prompt: list the orphaned keys, offer Delete / Keep
4. If Keep: rows remain in `kernel_settings` with `orphaned = 1` indefinitely (soft-delete). If the kernel is reinstalled at the same or higher version, rows are restored.
5. If Delete: stored values removed from `settings` table, rows removed from `kernel_settings`

---

## Settings UI Changes

- The Settings accordion currently renders groups from the in-memory schema. No structural change needed — kernel settings appear as new groups automatically once merged into the schema.
- `secret: True` schema hint → render value field as `<input type="password">` with a show/hide toggle.
- Orphan cleanup prompt → a dismissible banner at the top of the Settings page listing affected extractor names and a "Review" button that expands the orphaned keys with a Delete/Keep action per key.

---

## Implementation Scope

This is a significant, self-contained feature. It touches:

| File | Change |
|---|---|
| `core/settings.py` | Merge `kernel_settings` DB rows into live schema at startup |
| `core/registry.py` | Parse `SETTINGS`, enforce naming/version, write to `kernel_settings` |
| `core/manager.py` | Init `kernel_settings` table in `settings.db` |
| `api/routes/settings.py` | Expose orphan cleanup endpoint |
| `frontend/settings.html` | Orphan cleanup prompt, secret field rendering |
| All built-in extractors | Add `SETTINGS` block (Option A only) |
| `tests/` | Registry certification tests, upgrade/removal lifecycle tests |

---

## Development Strategy

**Must be done on a dedicated branch** — the DB schema change (`kernel_settings` table) and the registry contract change are breaking if partially applied. Recommended flow:

1. Create `feature/unified-settings` branch from master
2. Implement and test the full lifecycle (certification, upgrade, removal)
3. Run Option B coexistence first (no extractor changes needed)
4. Merge to master behind a feature flag or once fully tested
5. Schedule Option A migration (move built-in settings into extractor modules) as a separate subsequent branch

Do **not** attempt to land this as a series of small commits to master — the intermediate states are inconsistent (some kernels self-registering, some not, DB table may or may not exist).

---

## Open Questions (to resolve when implementation begins)

1. Should `SETTINGS` be optional (kernels without it are fine) or required for certification?
2. How do we handle a kernel that changes its `id` between versions (effectively a rename)?
3. Should orphaned settings be visible to the user in a "hidden" section, or fully invisible until cleanup is chosen?
4. Should `secret: True` values be stored encrypted, or is the existing plaintext `settings.db` acceptable given DocVault is local-only?
