# DocVault — System Architecture Reference

> Developer reference for the DocVault local-first document intelligence platform.

---

## 1. System Overview

DocVault runs as a single Python process that combines a FastAPI web server with three background daemon worker threads. The frontend is server-rendered HTML using the shared "Warm LCARS" design system (`lcars.css` / `lcars.js`, no build step, no Tailwind) and vanilla JavaScript, served directly by FastAPI's StaticFiles mount. All AI inference (embeddings, vision, chat) is delegated to a local Ollama instance via HTTP.

The system is intentionally monolithic — one process, one command to start, instant Ctrl+C to stop. Worker threads are daemon threads; they terminate immediately on process exit with no graceful drain (`run.py` forces `os._exit(0)` after a short grace period for the same reason). This is a deliberate trade-off: simplicity over graceful shutdown. Vector search is powered by **LanceDB**, an embedded Python library — it runs in-process and persists its data to `lancedb_storage/` on disk. No external service or Docker container is required for normal operation.

---

## 2. Component Map

| Component | File(s) | Role |
|---|---|---|
| Entry point | `run.py` | Starts FastAPI, spawns 3 worker threads, starts HardwareMonitor thread |
| API server | `api/main.py`, `api/routes/` | FastAPI app, all HTTP endpoints |
| Extraction worker | `workers/extraction_worker.py` | Claims PENDING tasks, runs kernels, writes text/metadata/images to DB |
| Embedding worker | `workers/embedding_worker.py` | Claims EXTRACTED tasks, chunks text, upserts embeddings to LanceDB |
| Art enrichment worker | `workers/art_worker.py` | Vision AI artwork identification, enriches the LanceDB art_index payload |
| Resource governor | `core/monitor.py` | HardwareMonitor daemon, ThrottleStateMachine, sensor registry |
| Extractor router | `core/router.py` | Maps file extensions to ordered lists of kernels |
| Kernel registry | `core/registry.py` | Discovers kernels via AST, certifies, detects tampered files |
| Task queue / DB layer | `core/manager.py` | SQLite layer: tasks, FTS5, vaults, settings, images, logs |
| Settings | `core/settings.py` | 3-tier (config.ini) and 4-tier (vault-aware) settings resolution |
| Vault manager | `core/vault_manager.py` | Vault CRUD and state machine (active/archived/gutted/deleted) |
| FTS search | `search/fts.py` | SQLite FTS5 full-text search |
| Semantic search | `search/semantic.py` | LanceDB vector search with graceful degradation |
| Hybrid search | `search/hybrid.py` | Weighted FTS + semantic (RRF); falls back to FTS-only on timeout/error |
| Extractor base | `core/extractors/base.py` | BaseExtractor, ExtractorContext, IngestResult, adapters |
| Kernels | `extractors/*.py` | Individual file-type extractor implementations |
| Frontend | `frontend/*.html` | Vault Status (index), Catalog, Search, Extractor Lab, Telemetry, Identity Hub |

---

## 3. Data Pipeline

```
┌──────────────────────────────────────────────────────────┐
│                       File on disk                        │
└──────────────────────────┬───────────────────────────────┘
                           │ Ingestor detects new / moved file
                           ▼
                  ┌─────────────────┐
                  │   docvault.db   │  tasks table: PENDING
                  └────────┬────────┘
                           │
            ┌──────────────▼──────────────┐
            │      Extraction Worker       │
            │  router picks kernel         │
            │  kernel.run(file_path, ctx)  │
            │  writes IngestResult to DB   │
            └──────────────┬──────────────┘
                           │ text, metadata, images written
                           ▼
                  ┌─────────────────┐
                  │   docvault.db   │  tasks: EXTRACTED
                  │   FTS5 index    │  text indexed
                  └────────┬────────┘
                           │
            ┌──────────────▼──────────────┐
            │      Embedding Worker        │
            │  sentence-aware chunking     │
            │  Ollama → embeddings         │
            │  upserts to LanceDB          │
            └──────────────┬──────────────┘
                           │
                  ┌─────────────────┐
                  │   docvault.db   │  tasks: EMBEDDED
                  │   LanceDB       │  vectors stored (lancedb_storage/)
                  └─────────────────┘
```

---

## 4. Three-Database Layout

| Database | File | Purpose | Survives reset.ps1? |
|---|---|---|---|
| Operational | `docvault.db` | Tasks, FTS5 index, extracted text, extracted images | **No** — deleted |
| Configuration | `settings.db` | User config, API keys, vault definitions, extractor registry | **Yes** — never touched |
| Observability | `logs.db` | Task timings, worker errors, system stats | Optional (asked interactively) |

**Why separate databases?**
`settings.db` must survive a clean-slate reset. Users may delete `docvault.db` to force re-extraction after changing settings — their API keys and vault configuration must never be at risk. Separate files make this guarantee trivial to implement and easy to audit.

---

## 5. Priority Scheduling

The task queue uses a composite priority formula to weight vault importance against extractor cost, with an age bonus preventing starvation:

```
priority = ((10 - vault_priority) * extractor_priority) + (age_seconds / 3600)
```

Higher values are processed first. Vault priority 1 (most important) produces the highest multiplier. The age bonus means every task will eventually be processed even if a high-priority vault is constantly adding new work.

---

## 6. Settings Resolution Chain

Four tiers, highest priority first:

1. **vault_settings** — per-vault overrides in `settings.db`
2. **settings.db** — user-set values via the web UI
3. **config.ini** — deployment defaults from `setup.ps1`
4. **Schema default** — built-in fallback in `core/settings.py`

**Critical invariant:** All configurable values must be read at call time inside functions, never at module import time. Reading at import time bypasses the resolution chain and produces stale, lowest-priority values.

---

## 7. Search Architecture

Three search modes, all returning `{results, degraded, degraded_reason}`:

- **Full-text (FTS5):** SQLite FTS5 against `extracted_text`. Supports wildcard (`word*`) and regex (`/pattern/` syntax). Always available.
- **Semantic:** LanceDB cosine similarity search (embedded, in-process) with `nomic-embed-text` embeddings. Wrapped in a per-step timeout (`search:semantic_timeout`, default 20s); returns `[]`/`None` gracefully on timeout or error.
- **Hybrid:** Reciprocal Rank Fusion of FTS + semantic results (`search/hybrid.py`). Falls back to FTS-only when the semantic step times out or errors, with `degraded: true` and a `degraded_reason` in the response.

Graceful degradation: the UI shows an amber banner when in degraded mode. No crash, no error page — just FTS results with a notice.

> **Vault-filtered search at scale:** when a search is scoped to a vault, `VectorStore.search()` avoids building a SQL `file_hash IN (...)` clause for very large hash sets (a vault with 100K+ documents would otherwise produce a multi-megabyte filter string that LanceDB takes 30s+ to evaluate). Past `_HASH_FILTER_INLINE_MAX` (500) hashes, it over-fetches an unfiltered ANN candidate pool and post-filters by Python `set` membership instead — see `embeddings/vector_store.py`.
