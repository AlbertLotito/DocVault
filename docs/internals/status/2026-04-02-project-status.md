# Project Status — 2026-04-02

Supersedes all earlier status documents.

---

## 1. Security Hardening — COMPLETE (2026-03-24)

### A. Configurable bind address
`run.py` reads `server:host` (default `127.0.0.1`) and `server:port` (default `8000`) from settings instead of hardcoding `0.0.0.0`.

### B. `open_path` vault jailing
`POST /api/utils/open_path` checks `_BLOCKED_EXTENSIONS` (21 types) and `_is_in_vault_root()` (symlink-safe, active+archived only) before any OS call.

### C. Disk sensor throttle wiring
`ThrottleThresholds` gains `disk_free_pct_throttle` (10%) and `disk_free_gb_throttle` (5 GB). `ThrottleStateMachine` checks both with OR logic. `lcars.js` disk tile shows GB + %. Tests: `tests/test_security.py` (15), `tests/test_disk_sensor.py` (11).

### D. Status doc
This document series.

---

## 2. Prompt Injection Hardening — COMPLETE (2026-03-24)

| Task | File | Change |
|------|------|--------|
| RAG system prompt | `llm/base.py` | Hardened with "UNTRUSTED DATA" warning + "Ignore any instructions" directive |
| RAG chunk wrapping | `llm/base.py` | Chunks wrapped in `<document index="N">`; `</document>` escaped to `&lt;/document&gt;` |
| Art result validation | `art_enrichment_worker.py` | `_validate_art_result()`: type-checks, clamps confidence, truncates strings |
| Injection flag | `core/extractors/base.py` | `_INJECTION_RE` + `_check_injection_patterns()` warns on suspicious extracted text |
| Tool-use prohibition | `llm/base.py` | Documented in `BaseLLMProvider.rag_query` docstring |

Tests: `tests/test_prompt_injection.py` (22 tests).

---

## 3. Search Enhancements — COMPLETE (2026-03-24)

| File | Change |
|------|--------|
| `search/query.py` | Shared `detect_mode()` — regex (`/pat/`), wildcard (`*`/`?`), plain |
| `search/fts.py` | Sanitizer preserves `word*` prefix; `/regex/` scan via REGEXP |
| `core/manager.py` | `filename_search()` handles regex, wildcard, plain; wildcard auto-wraps `%` |
| `api/routes/search.py` | regex/wildcard downgrades semantic/hybrid → FTS + `degraded_reason` |

Tests: `tests/test_search_enhancements.py` (30 tests).

---

## 4. Theme Editor — COMPLETE (2026-03-23)

New `/theme` page with live CSS variable inspector and preset support. `applyTheme()` + `lcThemeLoad()` in `lcars.js` — every page fetches and applies saved theme before `lc:ready`.

---

## 5. Archive X-Ray — PR OPEN (branch `feature-archive-xray`, PR #2)

| File | Change |
|------|--------|
| `core/archive_reader.py` | Shared `read_entries()`, `_classify()`, `_GROUP_ORDER` for ZIP/TAR/7Z/RAR |
| `extractors/archive_xray_extractor.py` | Refactored to import from `core.archive_reader` |
| `api/routes/catalog.py` | `GET /api/catalog/archive?path=...` — returns entries (capped 500), groups, compression stats, password flag |
| `frontend/search.html` | Archive badge + Browse toggle + collapsible panel |

Tests: `test_archive_reader.py` (15), `test_archive_browse_api.py` (8), `test_registry_certify.py` (3).

**Note:** `file_type` column stores last extension only (`backup.tar.gz` → `gz`). Content search results lack `file_type` — archive detection uses `isArchivePath()` extension parsing.

---

## 6. Embedding Progress Monitor + Fault-Tolerance Hardening — COMPLETE (2026-03-21)

- Real-time embedding progress in sensor rail and vault page.
- 12 brittle points eliminated. Root cause of prior 4-day freeze: double-exception pattern suppressing re-raise.
- `workers/embedding_worker.py`: inner try/except around `update_task_status()`; bare `raise` at same indent as inner try.
- `core/monitor.py`: `time.sleep()` outside try block — prevents spin-loop on sensor errors.

---

## 7. Font Scale — COMPLETE (2026-03-30, on master)

`--font-scale` CSS variable in `lcars.css :root`; all `font-size` rules use `calc(Xpx * var(--font-scale))`. Theme editor stepper: 80–140%, 10% steps, saved in `_overrides`. Tests: `test_font_scale_css.py` (4), `test_font_scale_ui.py` (5).

---

## 8. Bug Fixes — COMPLETE (2026-03-30, on master)

| Bug | Fix |
|-----|-----|
| Qdrant false-positive "unavailable" | `search/semantic.py` returns `None` on exception (was `[]`); `hybrid.py` checks `sem_r is None` |
| RAG 500 error | `api/routes/query.py`: `results, _ = await hybrid.async_search(...)` (was assigning the tuple directly) |
| Embedding worker silent crash | Python scoping: `from core.settings import settings` inside `except` block made `settings` an unbound local for the entire function. Removed inner import. |

---

## 9. RTF Extractor — COMPLETE (2026-04-02, on master)

`extractors/rtf_extractor.py` — uses `striprtf` (already in venv at 0.0.29). MANIFEST id: `com.docvault.document.rtf`. Decoding: utf-8 → latin-1 → cp1252 fallback. 26 RTF tasks reset to PENDING and processing.

---

## 10. Pipeline Stall Sensors — COMPLETE (2026-04-02, on master)

The existing `_check_stall()` only watched extraction. Embedding stalls were invisible — when Qdrant goes down or the worker crashes, the EXTRACTED queue backs up silently.

| File | Change |
|------|--------|
| `workers/embedding_worker.py` | `task_by_hash` dict + `batch_start` timer + `_record_timing()` loop after each batch → populates `task_timings` with `extractor='embedding'` |
| `core/manager.py` | `init_logs_db()`: idempotent ALTER TABLE adds `extracted_queue` and `embedding_queue` INTEGER columns to `system_stats` |
| `core/monitor.py` | `_embed_stall_detected/minutes/lock`, `get_embed_stall_state()`, `_set_embed_stall_state()`, `_check_embed_stall()` on `HardwareMonitor`. `_record_sample()` now reads queue counts from `docvault.db` first (separate connection), then writes to `logs.db` |
| `core/settings.py` | `monitor:embed_stall_threshold_mins` (default 5 minutes) |
| `api/routes/monitor.py` | `embed_stall`, `embed_stall_minutes`, `thresholds.embed_stall_threshold_mins` added. `ThrottleThresholds` converted via `dataclasses.asdict()` before adding key |
| `frontend/telemetry.html` | Pipeline Health section on HW tab: stall alert banner, 30-sample CSS bar chart (EXTRACTED=blue, EMBEDDING=purple), two stall sensor cards (green/amber/red). `/workers/status` added to `Promise.all` |
| `tests/test_pipeline_stall_sensors.py` | 10 tests — all passing |

**Non-obvious implementation details:**
- Cross-DB reads use two separate connections (no `ATTACH DATABASE`) — WAL mode on Windows causes locking with attached DBs.
- Mock patch target for `get_embed_stall_state` in API tests: `api.routes.monitor.get_embed_stall_state` (not `core.monitor`) — `from` import binds at import time.
- `_set_embed_stall_state` stores `stall_mins` always (not only when stalled) — required for amber state (`> 50% of threshold`) to be reachable.
- Fresh install with no embedding history: stall suppressed (no baseline yet). Stalls fire once first batch establishes a completion timestamp.

---

## 11. Current Backlog

### Archive X-Ray
- PR #2 on branch `feature-archive-xray` — needs review and merge.

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

## 12. Known Bugs

None currently tracked.
