# Project Status — 2026-03-15

Supersedes all earlier status documents.

---

## 1. LCARS UI Overhaul — COMPLETE

Replaced the Tailwind-based light UI with a unified **Warm LCARS** dark design system across every DocVault page. 13 commits, all on master.

### Design System

| File | Role |
|---|---|
| `frontend/static/lcars.css` | Shared CSS — custom properties, navbar, bar badges, stat tiles, vault cards, status badges, filter pills, table, modal, toast, context menu, scrollbar |
| `frontend/static/lcars.js` | Shared JS — navbar HTML injection, sensor rail polling (5s), `lcToast()`, `lcAccordion()` |

**Palette:** Amber/orange (`#ff9900`, `#cc6600`) on near-black (`#0a0600`, `#000`).
**Typography:** `'Arial Narrow', Arial, sans-serif` — minimum 10px for all visible text.
**Navbar:** Two-row sticky — top row: elbow + brand + 5 pill nav links; bottom row: live sensor rail (CPU / GPU / Temp / Disk / Workers / State), amber 3px bottom stripe.

### Page Inventory

| File | Status | Notes |
|---|---|---|
| `frontend/vault.html` | **New** | Merged `index.html` + `catalog.html`. Top: 6-tile stat grid + vault cards + unknowns strip. Divider. Bottom: filter pills + sortable table + pagination. Stat tile click → filter + scroll. URL param `?status=X` support. |
| `frontend/search.html` | **Restyled** | 3 LCARS bar badge mode tabs (Content / Filename / Ask). |
| `frontend/utils.html` | **Restyled** | LCARS hub — 6 bar badges. Top 3 navigate to full pages; bottom 3 (Health Check / Google Drive / Qdrant) accordion inline. |
| `frontend/optimizer.html` | **New** | Mission Control extracted from utils.html overlay to standalone page. Q1 (top-left) = Config + Throughput meters. Q2 (right) = System Log. Q3 (bottom-left) = Telemetry histograms. |
| `frontend/settings.html` | **Restyled** | 14 accordion bar badges replace the sidebar. `lcAccordion()` handles open/close. |
| `frontend/identity.html` | **Restyled** | LCARS card grid. `Find Photos` deep links updated `/catalog?query=` → `/vault?query=`. |
| `frontend/lab.html` | **Restyled** | LCARS navbar added. Emerald/ruby/indigo palette harmonized to Warm LCARS. |
| `frontend/telemetry.html` | **Restyled** | LCARS navbar added. Tab switcher → `lc-filter-pill`. Stuck tasks modal → `lc-modal-backdrop`. |

**Retired (kept on disk, no longer served):** `frontend/index.html`, `frontend/catalog.html`
**Deleted:** `frontend/static/mockup-*.html` (all 5 design mockups)

### Route Changes (`api/main.py`)

| Route | Before | After |
|---|---|---|
| `/` | `index.html` | `vault.html` |
| `/status` | `index.html` | `vault.html` |
| `/vault` | — | `vault.html` (new) |
| `/catalog` | `catalog.html` | 301 → `/vault` |
| `/optimizer` | — | `optimizer.html` (new) |

### Active Nav Pills

`data-page` attribute on `<body>` drives active pill detection in `lcars.js`:
- `lab`, `telemetry`, `optimizer` → highlight **Utilities** pill as parent
- All others → highlight their own pill

---

## 2. Pipeline Fix — normalize() Meta-Dropping Bug

Fixed three branches in `LegacyExtractorAdapter.normalize()` (`core/extractors/base.py`) that silently dropped the `meta` dict when wrapping extractor results into `IngestResult`. Also fixed `extractors/text_extractor.py` to always return a 3-tuple including `ocr_pages` even on the no-text error path. Commit: `8552339`.

**Effect:** `ocr_pages` is now correctly written mid-flight so `image_extractor` can read it; child task dispatch for scanned PDFs works correctly.

---

## 3. Backend Fix — Sort Columns

Added `file_size` and `vault_id` to `allowed_sort_by` in `core/manager.py → list_tasks()`. Previously these columns silently degraded to `last_update` ordering. Commit: `5a3ef03`.

---

## 4. Current TODO / Backlog

### Security Hardening (plan: `docs/internals/plans/2026-03-04-security-hardening.md`)
- A.1/A.2: server:host/port settings + read bind address in run.py (→ 127.0.0.1)
- B.1: Jail open_path to vault roots + block BLOCKED_EXTENSIONS
- C.1–C.6: Disk sensor full wiring (ThrottleThresholds, StateMachine, schema, logs, UI tile)
- D.1/D.2: Full test suite + offline rigs

### Prompt Injection Hardening (plan: `docs/internals/plans/2026-03-05-prompt-injection-hardening.md`)
- Tasks 1–5: RAG prompt hardening, XML chunk delimiters, art output validation, injection pattern flagging

### Art Collection Intelligence (plan: `docs/internals/plans/2026-03-07-art-collection-intelligence.md`)
- Tier 1–3: folder kernel, image child tasks, art_enrichment_worker + Google Vision + .nfo sidecars

### Other Backlog
- Face Crop Gallery UI in Identity Hub
- Archive X-Ray: header-level search for ZIP/TAR (plan: `docs/internals/plans/2026-03-13-archive-xray-implementation.md`)
- Unified Settings: kernel-specific env vars into Settings UI
- "Nerds" diagnostics page (logs.db dashboard)
- Search enhancements: wildcard/regex in filename + content search

### Disk Sensor Status (partial)
MonitorReading ✓ | DiskSensor ✓ | _merge_readings ✓ | _record_sample ✓
ThrottleThresholds disk fields: **NOT YET** | StateMachine disk check: **NOT YET** | Settings schema: **NOT YET** | UI tile: **NOT YET** (now part of LCARS sensor rail when complete)

### Known Bugs
- OCR charmap: ~1,013 tasks in ERROR — `ocr_extractor.py` encodes to cp1252; fix → ensure unicode str throughout
- `ollama_governor()`: `state = get_throttle_state()` wrong — function returns TUPLE. Fix: `state, reason = get_throttle_state()`
