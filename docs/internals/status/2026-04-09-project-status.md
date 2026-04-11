# Project Status — 2026-04-09

Supersedes all earlier status documents.

---

## 1–10. Previous Work — COMPLETE

See `docs/internals/status/2026-04-02-project-status.md` for full details on:
Security Hardening, Prompt Injection Hardening, Search Enhancements, Theme Editor,
Archive X-Ray (PR #2), Embedding Progress Monitor, Font Scale, Bug Fixes,
RTF Extractor, Pipeline Stall Sensors.

---

## 11. Per-Vault Ignore Rules — COMPLETE (2026-04-05, on master)

Allow users to exclude file extensions and folder patterns from ingestion scanning,
globally via Settings and per-vault via the Manage modal, with retroactive cleanup.

| File | Change |
|---|---|
| `core/manager.py` | `ignore_extensions` + `ignore_folders` columns added to `vaults` table |
| `core/settings.py` | `ingestion:ignore_extensions` + `ingestion:ignore_folders` global settings |
| `core/ingestor.py` | `parse_ignore_patterns()`; folder pruning + extension filter in `ingest()` |
| `core/vault_manager.py` | Added `ignore_extensions`, `ignore_folders` to `update_vault` allowlist |
| `run.py` | Passes `vault_row=vault` to `ingest()` |
| `api/routes/vaults.py` | `ignore-preview` + `apply-ignore` endpoints; two-pass delete to avoid long write locks |
| `frontend/vault.html` | Ignore fields in Identity tab; cleanup section in Maintenance tab |
| `frontend/settings.html` | Added `ingestion` group to GROUPS allowlist |
| `frontend/static/i18n/*.json` | `settings.group.ingestion` label in en/es/fr |
| `tests/test_vault_ignores.py` | 7 tests passing |

**Known remaining issue:** "Clean Up Noise" button reliability — brainstorming session
started for a proper redesign (A=reliability, B=feedback identified as pain points).
Brainstorming was interrupted and not yet completed.

---

## 12. Portability / New Machine Fixes — COMPLETE (2026-04-09, on master)

Fixes to make DocVault run on any machine without manual config edits.

| Commit | Fix |
|---|---|
| `4825fdf` | `config.ini`: `sqlite_path`, `cache_directory`, `poppler_path`, `credentials` paths made relative |
| `4825fdf` | `core/manager.py` `get_db_path()`: resolves relative paths against project root |
| `9fe1c18` | `core/registry.py`: removed `ast.Str` usage (removed in Python 3.14) |
| `82b99e9` | `config.ini`: `scan_directory` blanked out; `bootstrap_default_vault` skips when blank |
| `37df645` | `core/settings.py`: `ConfigParser(strict=False)` for Python 3.14 compatibility |
| `de60956` | `run.py` + `core/manager.py`: bootstrap reads `scan_directory` from `config.ini` directly, not `settings.db` |

---

## 13. Current Backlog

### In-Progress / Started
- **"Clean Up Noise" redesign** — brainstorming started, not completed.
  Pain points identified: A=reliability (DB lock), B=feedback (no progress/result info).
  Next step: resume brainstorming, write spec, write plan, implement.

### Open PR
- **Archive X-Ray** — PR #2 on `feature-archive-xray`, needs review and merge.

### Art Collection Intelligence
- Tier 1: `art_collection_extractor.py` — folder kernel, structural summary, child task queuing
- Tier 2: image child tasks → idle Ollama vision worker (descriptions)
- Tier 3: `art_enrichment_worker.py` + Google Vision API + `.nfo` sidecars + transactional rename
- Settings needed: `google:vision_api_key`, `art:enrichment_*`

### Span Grounding for RAG
- Store `chunk_offset` (start char position) alongside each chunk in FTS/Qdrant metadata
- RAG responses cite exact document location ("paragraph 4, line 67") instead of raw chunk text
- Enables source highlighting in document viewer
- Inspired by LangExtract `char_interval` pattern (article 2026-04-08)

### Other Backlog
- Face Crop Gallery UI in Identity Hub
- Unified Settings: kernel-specific env vars in Settings UI
- "Nerds" diagnostics page (`logs.db` dashboard)
- New vault wizard UI | Deleted file detection | Claude API key in Settings UI

---

## 14. Known Issues

- **WMI CPU temp sensor** — fails on some machines with COM error 0x80041003.
  `tools/test_wmi_temp.py` created to diagnose available sensors per-machine.
  Monitor falls back to dummy (0°C). Non-critical.

- **SQLite lock contention** — `apply-ignore` mitigated with two-pass approach.
  Workers doing long write transactions can still block short API writes.
  Root cause: no write serialisation across threads. Full fix deferred.

---

## 15. Two-Machine Setup Notes

| Machine | Path | Python |
|---|---|---|
| Primary (Albert's workstation) | `E:\DocVault` | 3.13 |
| Secondary | `C:\DocVault` | 3.14 |

- `settings.db` and `logs.db` persist across `docvault.db` resets (keep registry + config)
- `docvault.db` holds vault + task data (safe to delete for clean start)
- Claude Code memory: `C:\Users\Albert\.claude\projects\<E- or C->-DocVault\memory\`
  Copy memory folder between machines to carry session context
