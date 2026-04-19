# Project Status — 2026-04-19

Supersedes all earlier status documents.

---

## 1–11. Previous Work — COMPLETE

See `docs/internals/status/2026-04-09-project-status.md` for full details on:
Security Hardening, Prompt Injection Hardening, Search Enhancements, Theme Editor,
Archive X-Ray (merged master 826dfa1), Embedding Progress Monitor, Font Scale,
Bug Fixes, RTF Extractor, Pipeline Stall Sensors, Per-Vault Ignore Rules,
Portability / New Machine Fixes.

---

## 12. Stability Fixes — COMPLETE (2026-04-19, on master, ff17fa3)

| File | Fix |
|---|---|
| `core/logger.py` | `sys.stdout.flush()` wrapped in `try/except OSError` — closed console no longer crashes worker threads |
| `run.py` | Windows `CTRL_CLOSE_EVENT` suppressed via `SetConsoleCtrlHandler`; PID logged on startup |
| `api/routes/utils.py` | `Browse` button: replaced `tkinter.filedialog` (fatal `Tcl_AsyncDelete` crash from background thread) with PowerShell `FolderBrowserDialog` subprocess |
| `workers/utils.py` | New `paused_sleep(duration, step)`: sleeps while paused, breaks immediately on unpause |
| `workers/embedding_worker.py` + `extraction_worker.py` | Use `paused_sleep()` when reason == 'paused' — eliminates tight CPU spin loop |
| `core/vault_manager.py` | `_gut_vault`: batched deletes, separate short-lived connections, `file_vault` cleanup added |
| `api/routes/vaults.py` | Catch broad `Exception` on delete/gut routes, not just `VaultStateError` |
| `core/router.py` | Pass `sync_disk=False` from extractors-list endpoint — avoids full disk scan on every call |
| `frontend/vault.html` | Manage Vault modal opens immediately; data loads async — eliminates multi-second UI hang |
| `config.ini` | `[server]` section with port 8050 |

**Root causes of prior silent crashes identified:**
1. `Tcl_AsyncDelete` — tkinter used from background thread, fatal C-level process kill (fixed)
2. `OSError` on `sys.stdout.flush()` — console handle closed externally (fixed)
3. Windows console window closed while server running (mitigated via `CTRL_CLOSE_EVENT` handler)

---

## 13. SWF (Shockwave Flash) Extractor — COMPLETE (2026-04-19, on master, b53a120)

Pure-Python OCR-based extractor for newspaper/magazine SWF flipbooks.

| File | Description |
|---|---|
| `extractors/swf_extractor.py` | FWS/CWS/ZWS header parse, tag walker, JPEG2/3/4 + lossless bitmap decoders, Tesseract OCR per page image |
| `core/settings.py` | `swf:min_image_area` (default 160,000 px²), `swf:max_pages` (default 200) |
| `tools/test_swf.py` | Standalone SWF test rig: header parse, pyswf attempt, raw string scan |

**Key implementation notes:**
- Tag IDs: 21 = DefineBitsJPEG2 (char_id + raw JPEG, NO alpha_offset), 35 = JPEG3, 36 = JPEG4, 74 = DefineBitsLossless2
- The critical bug fixed during development: tag 21 had `skip_prefix=6` (treating it as JPEG3); correct is `skip_prefix=2`
- Tested on `APCP JAN 2017.swf`: 193 JPEG2 tags, 49 qualify above area threshold, OCR produces real newspaper text
- Activation: start server → Lab → swf_extractor shows as `unverified` → click Activate

---

## 14. LanceDB Migration — PLANNED (branch: feature-lancedb-migration)

Plan to replace Qdrant (requires Docker/external service) with LanceDB (embedded Python library, no external service).

| Detail | Value |
|---|---|
| Plan doc | `docs/internals/plans/2026-04-19-lancedb-migration.md` |
| Branch | `feature-lancedb-migration` |
| Tasks | 12 tasks (install → rewrite → cleanup → smoke test) |
| Core change | Rewrite `embeddings/vector_store.py` (~100 lines); 3 bypass sites to fix |

Not started. See plan doc for full task breakdown.

---

## 15. Current Backlog

### Open Branch
- **LanceDB Migration** — `feature-lancedb-migration`, not started. Plan written.

### In-Progress / Started
- **"Clean Up Noise" redesign** — brainstorming started, not completed.
  Pain points: A=reliability (DB lock), B=feedback (no progress/result info).
  Next step: resume brainstorming, write spec, write plan, implement.
- **Span Grounding for RAG** — Tasks 1–2 done on `feature-span-grounding`.
  Tasks 3–7 pending: `GET /api/catalog/{hash}/text`, query/search enrichment, frontend viewer.

### Art Collection Intelligence
- Tier 1: `art_collection_extractor.py` — folder kernel, structural summary, child task queuing
- Tier 2: image child tasks → idle Ollama vision worker (descriptions)
- Tier 3: `art_enrichment_worker.py` + Google Vision API + `.nfo` sidecars + transactional rename
- Settings needed: `google:vision_api_key`, `art:enrichment_*`

### Other Backlog
- Face Crop Gallery UI in Identity Hub
- Unified Settings: kernel-specific env vars in Settings UI
- "Nerds" diagnostics page (`logs.db` dashboard)
- New vault wizard UI | Deleted file detection | Claude API key in Settings UI

---

## 16. Known Issues

- **WMI CPU temp sensor** — fails on some machines with COM error 0x80041003.
  `tools/test_wmi_temp.py` created to diagnose. Monitor falls back to dummy (0°C). Non-critical.

- **SQLite lock contention** — `apply-ignore` mitigated with two-pass approach.
  Workers doing long write transactions can still block short API writes.
  Root cause: no write serialisation across threads. Full fix deferred.

---

## 17. Two-Machine Setup Notes

| Machine | Path | Python |
|---|---|---|
| Primary (Albert's workstation) | `E:\DocVault` | 3.13 |
| Secondary | `C:\DocVault` | 3.14 |

- `settings.db` and `logs.db` persist across `docvault.db` resets
- `docvault.db` holds vault + task data (safe to delete for clean start)
- Claude Code memory: `C:\Users\Albert\.claude\projects\<E- or C->-DocVault\memory\`
