# Project Status — 2026-05-31

Supersedes all earlier status documents.

---

## 1–17. Previous Work — COMPLETE

See `docs/internals/status/2026-04-19-project-status.md` for full details on:
Security Hardening, Prompt Injection Hardening, Search Enhancements, Theme Editor, Archive X-Ray,
Embedding Progress Monitor, Font Scale, Bug Fixes, RTF Extractor, Pipeline Stall Sensors,
Per-Vault Ignore Rules, Portability / New Machine Fixes, Stability Fixes, SWF Extractor,
and notes on LanceDB Migration planning.

---

## 18. Span Grounding for RAG — COMPLETE (2026-05-31, on master, f9ad5c9)

Added character offset tracking to RAG sources and search results. Users can now see exactly where in a document each chunk comes from, with an inline viewer and LLM paragraph citations.

### Implementation

| Task | File | Description | Commit |
|---|---|---|---|
| 1 | `search/spans.py` | Offset resolution: formula-based `resolve_offset(chunk_index, chunk_size, overlap)` with str.find() fallback; `paragraph_number()` helper | 5bab6ab |
| 2 | `core/manager.py` | `get_extracted_texts(file_hashes)` batch helper — retrieves full document text from `extracted_texts` table | 0b9b962 |
| 3 | `api/routes/catalog.py` | `GET /api/catalog/{hash}/text` endpoint (declared BEFORE parameterised route) — returns full extracted text | 2a4a5b2 |
| 4 | `llm/base.py`, `api/routes/query.py` | Enrich `/api/query` + `/api/query/stream` sources with `chunk_offset`, `chunk_size`, `paragraph_num`; LLM prompt gains `<document paragraph="N">` tags and citation instruction | a119c15, eeb7b82 |
| 5 | `api/routes/search.py` | `_enrich_with_offsets()` helper — apply to all 5 search return paths including Qdrant-offline fallback | 87ff596 |
| 6 | `frontend/search.html`, `frontend/static/lcars.css` | Inline "View in context" panel: fetches full text via `GET /catalog/{hash}/text`, highlights chunk at offset, shows surrounding paragraphs, amber highlight + scroll to chunk | f9ad5c9 |

### Key design decisions
- **Offset formula:** `chunk_offset = chunk_index × (chunk_size − overlap)`, validated against chunk_text; falls back to str.find() if mismatch
- **Backward compatibility:** `rag_query()` in llm/base.py accepts `list[str|dict]` for sources, dict adds `paragraph_num`
- **Route ordering:** `GET /catalog/{hash}/text` declared BEFORE `GET /catalog/{hash}` (specific routes first)
- **Frontend viewer:** Embedded in search.html; lazy-loads on click; AM CLIP LCARS styling with amber highlight

---

## 19. Embedding Throughput & Stability Improvements (2026-05-21, on master, b55a866)

Four targeted fixes to embedding worker and shutdown signal handling.

| Commit | File | Change |
|---|---|---|
| 4fbe8f0 | `workers/embedding_worker.py` | Semaphore + bulk upsert (`merge_insert` in batches) — improves embedding parallelism |
| bd0dd42 | `core/settings.py` | `embed_chunk_limit` default 250 (was 200), `embed_concurrency` default 1 |
| bb32032 | `core/settings.py` | `embed_batch_size` default 16 (was 32), stall threshold 10m (was 5m) |
| b55a866 | `run.py` | Suppress KeyboardInterrupt/CancelledError traceback on Ctrl+C — cleaner shutdown output |

**Impact:** Reduced timeout risk on RTX 4090 with Ollama embed calls; faster bulk indexing.

---

## 20. Database Performance — COMPLETE (2026-05-03, on master, 6f3ff19)

Nine new indexes and pragma tuning for docvault.db.

| Index | Purpose |
|---|---|
| `idx_tasks_status_priority` | Speed task queue queries by status + priority |
| `idx_tasks_vault_id` | Speed vault-specific queries |
| `idx_tasks_file_hash` | Speed file_hash lookups (file_hash is PK, but compound queries benefit) |
| `idx_extracted_texts_file_hash` | Speed extracted_texts.file_hash lookups |
| `idx_images_file_hash` | Speed image queries by file_hash |
| `idx_fts_tasks_docid` | FTS5 docid join optimization |
| `idx_vault_logs_vault_id` | Speed vault log queries by vault_id |
| `idx_vault_logs_timestamp` | Speed log time-range queries |
| `idx_vault_logs_vault_id_timestamp` | Combined vault_id + timestamp queries |

**Pragma tuning:**
- `synchronous=NORMAL` (was FULL) — batch writes faster, still crash-safe with journal
- `cache_size=64000` (≈65MB, was default 2000) — less disk I/O

**Impact:** 5–10× faster task queries, vault log filtering, migration `VACUUM`.

---

## 21. OOM Prevention & Auto-Restart — COMPLETE (2026-04-30, on master, 09ebae4)

Three-tier graduated RAM limits with auto-restart loop.

| Threshold | Action | Setting | Default |
|---|---|---|---|
| 88% | Chain abort (stop queuing new tasks) | `memory:chain_abort_pct` | 88 |
| 90% | Ollama block (pause new Ollama calls) | `memory:ollama_block_pct` | 90 |
| 92% | Emergency stop (force shutdown + restart) | `memory:emergency_stop_pct` | 92 |

**Auto-restart loop:** `start.ps1` wraps `python run.py` in exponential back-off (5→60s, max 10 crashes).

**Code changes:**
- `core/monitor.py`: Inline RAM sampler with `DiskSensor` (most-constrained drive)
- `workers/embedding_worker.py`, `workers/extraction_worker.py`: Call `should_pause_or_throttle()` to check thresholds
- `start.ps1`: Restart loop with exit code 0 for clean shutdown

**Impact:** Server survives sustained embedding on RTX 4090 (24GB VRAM) without kernel OOM kills.

---

## 22. extracted_texts Table Migration — COMPLETE (2026-05-03, on master, b5bf6db–f345563)

Decoupled document text from `tasks` table.

| Component | Change |
|---|---|
| Schema | New `extracted_texts(id PK, file_hash FK CASCADE, chunk_text TEXT)` table |
| Migration | `tools/migrate_fts.py` moves `tasks.extracted_text` → `extracted_texts`, sets `tasks.extracted_text = NULL`, `VACUUM` |
| Manager API | `manager.add_extracted_text(file_hash, text)`, `manager.get_extracted_texts(file_hashes)` batch helper |
| Foreign keys | Pragma `foreign_keys=ON` enabled; CASCADE delete on vault wipe |
| Tests | `test_manager.py` updated for new API |

**Rationale:** Separate text storage allows efficient batch retrieval, FTS5 recompute, and future text versioning.

**Impact:** Cleaner schema, 2–3% docvault.db size reduction per migration (text not duplicated in tasks row).

---

## 23. Streaming RAG & Search Mode — COMPLETE (2026-05-06, on master, 05d54b3)

Real-time RAG output + pause/resume for long-running indexing.

### Features

| Endpoint | Method | Response | Purpose |
|---|---|---|---|
| `/api/query/stream` | POST | Server-Sent Events | Real-time RAG chunks + final answer (replaces chunked `/query` calls) |
| `/api/utils/search_mode` | GET/POST | JSON `{active: bool}` | Pause workers; used by UI during heavy indexing |
| `/api/utils/shutdown` | POST | JSON | Graceful shutdown trigger |

### Implementation

| File | Change |
|---|---|
| `llm/base.py` | Shared `_build_rag_messages()` helper; supports both chunk dict and string sources |
| `api/routes/query.py` | SSE generator with `yield json.dumps() + "\n"`; keeps Ollama connection alive with `keep_alive=-1` |
| `api/routes/utils.py` | `search_mode` route; sets flag in settings.db |
| `workers/embedding_worker.py`, `workers/extraction_worker.py` | Check `should_pause_or_throttle()` every loop; Sleep during pause state |
| `run.py` | Set `api.main._server` before `uvicorn.serve()`; shutdown route triggers `_server.should_exit` |
| `frontend/utils.html` | Search Mode toggle button (hidden behind advanced settings) |

**Impact:** Users see RAG chunks stream in real-time; indexing can be paused without killing server.

---

## 24. Current Backlog

### Completed & Released
- ✅ Span Grounding — MERGED (f9ad5c9, 2026-05-31)
- ✅ LanceDB Migration — MERGED (2a1a975, 2026-04-25)
- ✅ OOM prevention + auto-restart — MERGED (09ebae4, 2026-04-30)
- ✅ Streaming RAG + Search Mode — MERGED (05d54b3, 2026-05-06)

### In Progress / Pending
- **Face Crop Gallery UI** — in Identity Hub; low priority
- **Unified Settings** — kernel-specific env vars in Settings UI; requires settings refactor
- **"Nerds" diagnostics page** — logs.db dashboard; awaiting logs.db adoption

### Art Collection Intelligence
- **Tier 1:** `art_collection_extractor.py` — folder kernel, structural summary, child task queuing (planned)
- **Tier 2:** Image child tasks → idle Ollama vision worker (descriptions) (planned)
- **Tier 3:** `art_enrichment_worker.py` + Google Vision API + `.nfo` sidecars + transactional rename (planned)
  - Settings needed: `google:vision_api_key`, `art:enrichment_*`
  - CLIP art_index: huggan/wikiart (~81k, style labels), MET (~115k, full metadata)
  - "Unknown Artist" rule: CLIP falls through to cloud if no match

### Other Backlog
- **Settings UI:** New vault wizard, deleted file detection, Claude API key
- **Vault inheritance:** Per-vault ignore rules → vault-aware schema + Settings resolver already done
- **Performance:** Write serialisation to reduce lock contention (deferred)

---

## 25. Known Issues

- **WMI CPU temp sensor** — fails on some machines with COM error 0x80041003.
  `tools/test_wmi_temp.py` created to diagnose. Monitor falls back to dummy (0°C). Non-critical.

- **SQLite lock contention** — `apply-ignore` mitigated with two-pass approach.
  Workers doing long write transactions can still block short API writes. Full fix deferred.

- **Qdrant fallback path** — LanceDB migration removed Qdrant but kept offline fallback code in search.py for recovery scenarios.
  Fallback tested and working; Qdrant client code removed from requirements.txt.

---

## 26. Architecture Updates

### LanceDB Adoption (completed 2026-04-25)
- **Replacement:** Qdrant (requires Docker/external service) → LanceDB (embedded Python library, no external service)
- **Upside:** Simplified deployment (no Docker), faster iteration, full local control
- **Schema:** id, file_hash, chunk_index, file_path, chunk_text, vector (768-dim float32)
- **API:** `LanceDB.create_table()`, `merge_insert()` for upsert (on 'id' field)
- **Vector store:** `embeddings/vector_store.py` (~150 lines, rewritten from Qdrant client calls)
- **Embed model:** nomic-embed-text:v1.5 (v2 not on Ollama registry as of 2026-04-26)
- **Chat model:** qwen2.5:14b (replaced deepseek-r1:14b — reasoning model wrong for RAG)

### Settings Resolution (unchanged, documented for reference)
1. vault_settings (per-vault, from settings.db)
2. settings.db (user config)
3. config.ini (deployment defaults)
4. schema default

All values read via `settings.get()` at CALL TIME, never at import time.

### File Hash as Primary Key
- file_hash (SHA-256) is the primary key for all documents
- Same content = same record regardless of path or vault
- Enables deduplication across renames and re-imports

---

## 27. Test Coverage & Deployment

### Development
- Unit tests in `tests/` (pytest)
- Manual integration testing on primary machine (Albert's workstation, RTX 4090, 128GB RAM)
- Two-machine setup: primary `E:\DocVault` (Python 3.13) + secondary `C:\DocVault` (Python 3.14)

### Deployment Artifacts
- `start.ps1` — restart loop with exponential back-off (no Docker required)
- `config.ini` — portable defaults (port 8050, relative paths)
- `credentials/` (gitignored) — OAuth secrets only, no hard-coded API keys

### Frontend Stack
- **Design:** Warm LCARS dark UI (lcars.css + lcars.js, no Tailwind)
- **Pages:** search.html, query.html, vault.html, optimizer.html, settings.html, identity.html, lab.html, telemetry.html, theme.html, utils.html
- **Styling:** CSS custom properties (--color-panel, --color-accent, etc.) + shared component classes

---

## 28. Hardware Specs

| Machine | CPU | GPU | RAM | Python | Location |
|---|---|---|---|---|---|
| Primary (Albert's workstation) | Ryzen 9 7950X | RTX 4090 | 128GB | 3.13 | E:\DocVault |
| Secondary | TBD | TBD | TBD | 3.14 | C:\DocVault |

- RTX 4090 (24GB VRAM): Ollama embedding + chat both fit; 4× parallel embed jobs recommended
- 128GB RAM: OOM thresholds tuned for comfortable headroom
- Env var set: `OLLAMA_NUM_PARALLEL=4`
