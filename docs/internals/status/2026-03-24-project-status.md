# Project Status — 2026-03-24

Supersedes all earlier status documents.

---

## 1. Security Hardening — COMPLETE

### A. Configurable bind address

`run.py` was hardcoded to `host="0.0.0.0"`, exposing DocVault on all network interfaces by default.

| File | Change |
|---|---|
| `core/settings.py` | Added `server:host` (default `127.0.0.1`) and `server:port` (default `8000`) to the `server` settings group. |
| `run.py` | Reads `server:host` and `server:port` at startup instead of hardcoding. Default is now loopback-only. |
| `tests/test_security.py` | Tests: schema entries present, default is `127.0.0.1`. |

### B. `open_path` vault jailing

`POST /api/utils/open_path` previously accepted any filesystem path and called `os.startfile()` on it — a trivial SSRF / arbitrary file execution vector.

| File | Change |
|---|---|
| `api/routes/utils.py` | Added `_BLOCKED_EXTENSIONS` set (21 dangerous types: `.exe`, `.bat`, `.ps1`, `.dll`, `.lnk`, etc.). Added `_is_in_vault_root()` using `os.path.realpath()` to block symlink escapes; only `active` and `archived` vaults count. `open_path` checks extension then vault membership before any OS call. |
| `tests/test_security.py` | Tests: blocked extensions, vault jailing, symlink escape, gutted-vault exclusion. 15 tests total. |

### C. Disk sensor throttle wiring

`DiskSensor`, `MonitorReading.disk_free_*` fields, and `_record_sample` were in place but the throttle state machine never acted on disk pressure.

| File | Change |
|---|---|
| `core/monitor.py` | `ThrottleThresholds` gains `disk_free_pct_throttle` (10%) and `disk_free_gb_throttle` (5 GB). `ThrottleStateMachine.update()` checks both with OR logic; `> 0` guard prevents dummy-sensor zeros from false-triggering. `_load_thresholds()` reads both new keys. |
| `core/settings.py` | Added `monitor:disk_free_pct_throttle` and `monitor:disk_free_gb_throttle` to the `monitor` settings group. |
| `frontend/static/lcars.js` | Fixed inverted `_diskClass()` (field is free %, so low = bad). Disk tile now shows both GB and % (e.g. `12.3G 45%`). |
| `tests/test_disk_sensor.py` | 11 tests: fields, defaults, pct trigger, GB trigger, OR logic, healthy=normal, dummy-zero guard, reason string, schema entries, `_load_thresholds`. |

---

## 2. Theme Editor — COMPLETE (2026-03-23)

New `/theme` page: side-by-side LCARS preview pane and CSS variable inspector. Changes apply live across all pages via CSS custom properties stored in `settings.db`.

| File | Change |
|---|---|
| `frontend/theme.html` | New page. Preview pane with `data-th-group` regions; inspector with 4 presets, 6 collapsible variable groups, Save/Revert/Reset footer. |
| `frontend/static/lcars.js` | Added `applyTheme(overrides)` and `lcThemeLoad()`. Every page fetches and applies saved theme inside `lcInit()` before `lc:ready`. Theme nav pill added after Settings. |
| `frontend/static/i18n/*.json` | Added `nav.theme` key to `en`, `es`, `fr`. |
| `api/routes/settings.py` | Added `GET /api/settings/theme` (returns `{"theme": {...}}`). Registered before `GET /api/settings` to avoid route shadowing. |
| `core/settings.py` | Added `ui:theme` schema entry (`hidden: True`). `get_all_configurable()` filters hidden entries. |
| `tests/test_theme.py` | 6 tests: schema, hidden key, endpoint defaults, monkeypatched save, corrupt JSON fallback, page HTML. |

---

## 3. Bug Fix — pypdf Surrogate Encoding (2026-03-23)

Memory recorded this as "OCR charmap / cp1252 / 1,013 tasks" — the actual DB had 1 affected task with a different error.

**Root cause:** `pypdf.extract_text()` produces lone surrogates on some malformed PDF encodings. Writing such a string to SQLite (UTF-8) raises `'utf-8' codec can't encode character '\udfXX': surrogates not allowed`.

**Fix:** `extractors/text_extractor.py` — sanitise immediately after `page.extract_text()`:
```python
text = raw.encode('utf-8', errors='ignore').decode('utf-8')
```
The 1 affected task was requeued.

---

## 4. Previous Work (still current)

### Embedding Progress Monitor (2026-03-21)
Real-time embedding progress in the sensor rail and vault page. Eliminates false "pipeline stuck" alarms for large files.

### Fault-Tolerance Hardening (2026-03-21)
12 brittle points eliminated. Root cause of 4-day freeze: double-exception pattern suppressing re-raise. See 2026-03-21 status doc for full detail.

### LCARS UI Overhaul (2026-03-15)
Full Warm LCARS dark design system across all pages. See 2026-03-15 status doc.

### Qdrant Resilience + Management Scripts (2026-03-14)
`restart: unless-stopped`, degraded-mode search, `start.ps1` / `setup.ps1` / `reset.ps1`.

---

## 5. Current TODO / Backlog

### Prompt Injection Hardening
- Task 1: Harden RAG system prompt (add "untrusted data" warning)
- Task 2: Wrap RAG chunks in XML delimiters, escape `</document>` breakout
- Task 3: Validate art enrichment structured outputs (`_validate_art_result`)
- Task 4: Flag injection patterns in extracted text → `logs.db worker_errors`
- Task 5: Document RAG LLM tool-use prohibition

### Art Collection Intelligence
- Tier 1: `art_collection_extractor.py` — folder kernel, structural summary, child task queuing
- Tier 2: image child tasks → idle Ollama vision worker
- Tier 3: `art_enrichment_worker.py` + Google Vision API + `.nfo` sidecars + transactional rename

### Search Enhancements
- Wildcard/regex in filename search (auto-detect `*`, `?`, `/pattern/`)
- FTS wildcard (`word*`), regex (`/pattern/` → REGEXP) in content search
- Hybrid + wildcard/regex: fall back to FTS-only with UI note

### Other Backlog
- Face Crop Gallery UI in Identity Hub
- Archive X-Ray: header-level search for ZIP/TAR
- Unified Settings: kernel-specific env vars in Settings UI
- "Nerds" diagnostics page (`logs.db` dashboard)
- New vault wizard UI | Deleted file detection | Claude API key in Settings UI

---

## 6. Known Bugs

None currently tracked.
