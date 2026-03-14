# Project Status & Design Notes — 2026-03-03

This document is the authoritative reference for the current state of the DocVault project. It supersedes all earlier status documents.

---

## 1. Project Summary

DocVault is a local-first document intelligence platform. It scans one or more designated folders ("vaults"), extracts text and metadata from a wide variety of file types, and makes all content searchable through a web interface. It combines full-text search (SQLite FTS5) with semantic vector search (Qdrant + Ollama embeddings) and supports Retrieval-Augmented Generation (RAG) to answer natural-language questions grounded in document content.

---

## 2. Core Philosophy: Maximum Extraction

DocVault's guiding principle is **maximum extraction**: extract as much information as possible from every file found. No file should be dark.

- Every page of every PDF is OCR'd if it has no text layer (or a sparse one).
- Images embedded in PDFs are described and transcribed by a vision model; descriptions are appended to the parent document's extracted text.
- Standalone images receive a full visual description plus an OCR pass.
- Audio files are transcribed via Whisper.
- Video files yield both a Whisper transcript of the audio track and vision-model descriptions of sampled frames.
- Google Drive stub files (`.gdoc`, `.gsheet`, etc.) are exported via the Drive API and processed by the appropriate existing extractor.
- Filenames are prepended to chunk text before embedding, so filename-based queries find the right file semantically.

The multi-stage approach means cheap methods run first and expensive methods (vision model, full-page render + OCR) are only invoked when necessary.

---

## 3. Current Status: **Operational — Infrastructure Migration Complete**

The application is fully operational and has been upgraded through a four-layer infrastructure migration:

- **Multiple vaults:** DocVault can now manage multiple scan roots ("vaults"), each with its own name, directory, priority, and per-vault settings overrides.
- **Resource governor:** A hardware monitor daemon measures GPU/CPU temperatures and RAM usage, and throttles or pauses workers automatically when the system is under thermal pressure.
- **Structured observability:** A third database (`logs.db`) captures per-extractor timings, worker errors, and system-stats samples.
- **Extractor contracts:** All extractors are wrapped in a `LegacyExtractorAdapter` that conforms to the new `BaseExtractor` / `ExtractorContext` / `IngestResult` contract, enabling cooperative cancellation and per-extractor timeout configuration.
- **Vault-aware settings:** A `SettingsResolver` provides 4-tier lookup: vault_settings → settings.db global → config.ini → schema default.
- **Composite priority scheduling:** Workers now claim tasks via a formula that balances vault priority, extractor priority, and task age.

---

## 4. Feature & Fix History

### Sessions 1–5 — 2026-02-26 to 2026-03-02

See the 2026-03-02 status document for detailed notes on:
- Core extraction pipeline (PDF multi-stage, vision model, Whisper, Google Drive)
- FTS5 + Qdrant semantic search with hybrid RRF merge
- Settings system (3-tier lookup), settings.db separation
- DB inspector modal, move detection, extracted artefact cache
- RAG chat with conversation history, source citations, thinking accordion
- Video frame description, three-tab search page

---

### Session 6 — 2026-03-03 — Infrastructure Migration

#### Layer 1: Database Schema

| Change | Detail |
|---|---|
| `vaults` table (settings.db) | `vault_id` (UUID PK), `name`, `scan_directory`, `priority`, `color`, `state` (`active`/`archived`/`gutted`/`deleted`), `created_at`, `updated_at` |
| `tasks.vault_id` column | Added to docvault.db; all existing tasks assigned to the auto-created "Documents" default vault |
| `vault_settings` table (settings.db) | Per-vault key/value overrides; same key format as global settings |
| `logs.db` | New third database with five tables: `task_timings`, `worker_errors`, `worker_log`, `extractor_stats`, `system_stats` |
| `bootstrap_default_vault()` | Creates "Documents" vault from current `paths:scan_directory` on first startup; idempotent |

Gate rig: `tests/rigs/check_db_migration.py` — **17/17 pass**

#### Layer 2: Extractor Contracts, Settings Resolver, Composite Priority, Resource Governor

**Extractor contract system (`core/extractors/base.py`)**

- `ExtractorContext` — per-call execution environment: `vault_id`, `file_hash`, `cancel_token`, `logger`, `settings`, `timeout_secs`
- `IngestResult` — structured output: `text`, `metadata`, `images`, `child_tasks`, `enrichments`, `errors`, `status`, `extractor_name`, `elapsed_secs`
- `BaseExtractor` — abstract contract: `run(file_path, ctx) → IngestResult`
- `LegacyExtractorAdapter` — wraps existing `(result, err)` extractors in the new contract; existing extractor code unchanged
- Extractor type taxonomy: `Utility`, `Intelligent`, `Chat`, `Pipeline`, `Remote`, `Archive`, `Synthesis`

**SettingsResolver (`core/settings.py`)**

- Lightweight per-call object: `SettingsResolver(vault_id=None, _settings_obj=None)`
- 4-tier chain: vault_settings → settings.db global → config.ini → schema default
- Defaults to the global `settings` singleton so the full chain (including `config.ini`) is preserved
- Global `settings` singleton untouched — backward compatible

**Composite priority scheduling (`core/manager.py` — `claim_pending_task`)**

Formula: `((10 − vault_priority) × extractor_priority) + (age_seconds / 3600)` ORDER BY DESC

- Lower vault_priority number = higher scheduling weight
- Extractor priority (from `router.py` PRIORITIES) remains a multiplier
- Age bonus grows at 1 point per hour — ensures no task starves indefinitely
- LEFT JOIN on `vaults` table; defaults to vault_priority=5 if vault row is missing

**Resource governor (`core/monitor.py`)**

- `MonitorReading` — snapshot dataclass: cpu_pct, cpu_temp, ram_pct, gpu_temp, gpu_util_pct
- Sensor abstraction: `BaseSensor` abstract → `NvidiaSensor` (pynvml), `PSUtilSensor` (cpu/ram via psutil), `WMICPUTempSensor` (Windows WMI), `DummySensor` (always returns 0s — fallback if hardware queries fail)
- Fallback sensor is NOT persisted; re-tested on every startup
- `ThrottleStateMachine` — Normal → Throttled → Cooldown state machine
  - Normal → Throttled: GPU temp > `monitor:gpu_temp_throttle` (80°C) or RAM > `monitor:ram_throttle_pct` (85%)
  - Throttled → Cooldown: pressure sustained for `monitor:sustained_minutes` (3 min) consecutive samples, OR GPU temp > `monitor:gpu_temp_cooldown` (88°C) immediately
  - Cooldown → Normal: pressure clears AND `monitor:cooldown_minutes` (10 min) have elapsed
  - Throttled → Normal: pressure clears before sustained threshold reached
- `HardwareMonitor` — daemon thread, samples at `monitor:sample_interval` (60s), updates module-level state
- `get_throttle_state()` — thread-safe read by workers

Gate rigs: `tests/rigs/check_settings_resolution.py` **4/4 pass** · `tests/rigs/check_resource_governor.py` **6/6 pass** · `tests/rigs/check_worker_priority.py` **2/2 pass**

#### Layer 3: VaultManager, Vault API, Multi-Vault Ingestor

**VaultManager (`core/vault_manager.py`)**

- `create_vault(name, scan_directory, priority, color)` — creates vault record; rejects duplicate `scan_directory`
- State machine — `active → archived → gutted → deleted`; `archived → active` (restore)
- `_gut_vault(vault_id)` — wipes FTS, extracted_images, tasks rows, and all Qdrant vectors for that vault's documents (source files untouched)
- `reindex(vault_id)` — resets COMPLETED tasks to EXTRACTED and removes Qdrant vectors to trigger re-embedding
- `VaultStateError`, `VaultConflictError` — clean exception types for state violations and duplicate-directory conflicts

**Vault API routes (`api/routes/vaults.py`)**

| Endpoint | Purpose |
|---|---|
| `GET /api/vaults` | List all vaults |
| `POST /api/vaults` | Create vault |
| `GET /api/vaults/{vault_id}` | Get single vault |
| `DELETE /api/vaults/{vault_id}?mode=archive\|gut\|delete` | State-machine transition |
| `POST /api/vaults/{vault_id}/restore` | archived → active |
| `POST /api/vaults/{vault_id}/reindex` | Trigger re-embedding for vault |
| `GET /api/vaults/{vault_id}/settings` | List per-vault setting overrides |
| `POST /api/vaults/{vault_id}/settings` | Set per-vault override |
| `DELETE /api/vaults/{vault_id}/settings/{key}` | Remove per-vault override |

**Monitor API route (`api/routes/monitor.py`)**

- `GET /api/monitor/status` — returns `{state, reading, thresholds}` from the live HardwareMonitor

**Multi-vault ingestor**

- `run.py` — `ingestion_worker_run()` now calls `VaultManager.list_vaults()` and calls `ingest(vault.scan_directory, db_path, vault_id=vault.vault_id)` for each active vault
- `core/ingestor.py` — `ingest()` accepts `vault_id=None`, passes to `insert_task()`
- `core/manager.py` — `insert_task()` and `list_tasks()` both accept `vault_id=None`

Gate rig: `tests/rigs/check_vault_api.py` — HTTP rig (requires server running; tested manually)

#### Layer 4: UI

| Task | Changes |
|---|---|
| Nav update | All 5 pages updated: Search · Vault Status · Vault Log · Utilities · Settings |
| Vault Status page (`index.html`) | Resource strip (GPU/CPU/RAM, polls `/api/monitor/status` every 10s); per-vault cards (polls `/api/vaults` every 30s with state badge, priority, stats, action buttons); compact 7-stat summary row; worker controls |
| Vault Log (`catalog.html`) | Title → "Vault Log"; vault filter dropdown added; `vault_id` param passed to backend `list_tasks()` |
| Search vault selector (`search.html`) | Vault checkboxes above the tab bar; vault_ids passed to all three search paths |
| Monitor settings (`core/settings.py` + `frontend/settings.html`) | 9 `monitor:*` keys added to schema (governor enable/disable, sample interval, temp/util thresholds, sustained/cooldown minutes, RAM throttle, CPU temp); Monitor tab renders automatically |
| Clear Logs (`api/routes/utils.py` + `frontend/utils.html`) | `POST /utils/clear_logs` endpoint; Diagnostics card with "Clear Logs Database" button |

#### Workers Updated

**`workers/utils.py`** — Rewrote `should_pause_or_throttle()` to return a `(skip, reason)` tuple:
- Returns `(True, 'paused')` if admin has paused workers
- Returns `(True, 'cooldown')` if governor is in cooldown state
- Returns `(False, 'throttled')` if governor is throttling (5s inter-task sleep added)
- Added `get_throttle_sleep(state)` — maps state to sleep seconds: normal=0, throttled=5, cooldown=30

**`workers/extraction_worker.py`** — Full rewrite:
- Uses `LegacyExtractorAdapter` and `ExtractorContext` for each extractor call
- Records per-extractor timings to `logs.db` via `_record_timing()`
- Cancellation support: checks `result.status == 'cancelled'`, resets task to PENDING
- Throttle-aware: checks `should_pause_or_throttle()` at top of each loop iteration

**`workers/embedding_worker.py`** — Targeted updates:
- `_load_vector_store(vault_id)` uses `SettingsResolver(vault_id)` for Qdrant host/port
- Pause check replaced with `should_pause_or_throttle()` + `get_throttle_sleep()`

---

## 5. Complete File Map

```
E:\DocVault\
├── run.py                          Entry point — FastAPI + worker + monitor daemon threads
├── config.ini                      Machine-specific defaults (all values set)
├── docvault.db                     SQLite: tasks (with vault_id), FTS5, extracted_images
├── settings.db                     SQLite: settings, pause state, vaults, vault_settings
├── logs.db                         SQLite: task_timings, worker_errors, worker_log, extractor_stats, system_stats
│
├── api/
│   ├── main.py                     FastAPI app, router mounts, GET /status, static + page routes
│   └── routes/
│       ├── catalog.py              GET /catalog (vault_id filter), GET /catalog/inspect, GET /catalog/{hash}
│       ├── search.py               GET /search, GET /search/filename
│       ├── query.py                POST /query (RAG)
│       ├── workers.py              GET/POST /workers (pause/resume/stats)
│       ├── utils.py                GET /utils/qdrant_check, POST /utils/reindex, POST /utils/clear_logs,
│       │                           GET /utils/gdrive_status, POST /utils/gdrive_authorize,
│       │                           POST /utils/open_path
│       ├── settings.py             GET /settings, POST /settings
│       ├── vaults.py               CRUD + state machine + per-vault settings (/api/vaults/...)
│       └── monitor.py              GET /api/monitor/status
│
├── core/
│   ├── ingestor.py                 Walks scan dir, detects new+moved files, accepts vault_id
│   ├── manager.py                  SQLite layer: tasks (vault_id), FTS5, settings, vaults, logs
│   ├── monitor.py                  HardwareMonitor daemon, sensor abstraction, ThrottleStateMachine
│   ├── router.py                   ROUTES dict: ext → [extractors], PRIORITIES dict
│   ├── settings.py                 Settings singleton (3-tier) + SettingsResolver (4-tier vault-aware)
│   ├── vault_manager.py            VaultManager: CRUD, state machine, gut, reindex
│   └── extractors/
│       ├── __init__.py
│       └── base.py                 BaseExtractor, ExtractorContext, IngestResult, LegacyExtractorAdapter
│
├── extractors/
│   ├── text_extractor.py           pypdf text layer
│   ├── image_extractor.py          PDF page rendering → Tesseract/vision; saves to cache dir
│   ├── word_extractor.py           python-docx
│   ├── excel_extractor.py          openpyxl
│   ├── pptx_extractor.py           python-pptx
│   ├── plaintext_extractor.py      UTF-8/Latin-1/CP1252 text files
│   ├── ocr_extractor.py            Standalone images → vision model + Tesseract fallback
│   ├── transcriber.py              Whisper audio transcription
│   ├── video_extractor.py          ffmpeg frames + vision descriptions + Whisper transcript
│   ├── metadata_extractor.py       Audio/video metadata (mutagen/ffprobe)
│   ├── unknown_extractor.py        Flags unrecognised file types
│   ├── gdrive_extractor.py         Google Drive stubs → Drive API export → sub-extractor
│   └── vision.py                   Shared Ollama vision utility (describe + transcribe)
│
├── embeddings/
│   ├── chunker.py                  Sliding window text chunker
│   └── embedder.py                 Ollama embedding client
│
├── search/
│   ├── fts.py                      FTS5 query wrapper (sanitizes input)
│   ├── semantic.py                 Qdrant vector search (sync + async)
│   └── hybrid.py                   RRF merge of FTS + semantic results
│
├── llm/
│   ├── base.py                     RAG prompt template + <think> block extractor
│   ├── factory.py                  Returns OllamaProvider or ClaudeProvider from settings
│   ├── ollama_provider.py          Ollama chat completion
│   └── claude_provider.py          Anthropic API chat completion
│
├── workers/
│   ├── extraction_worker.py        Polls PENDING tasks; uses LegacyExtractorAdapter + ExtractorContext
│   ├── embedding_worker.py         Polls EXTRACTED tasks; chunks, embeds, writes Qdrant
│   └── utils.py                    should_pause_or_throttle(), get_throttle_sleep(), interruptible_sleep()
│
├── frontend/
│   ├── index.html                  Vault Status — resource strip, per-vault cards, worker controls
│   ├── catalog.html                Vault Log — vault filter dropdown, paginated task log
│   ├── search.html                 Three-tab: Search | Filename | Ask; vault selector
│   ├── utils.html                  Qdrant health check, Google Drive auth, Clear Logs
│   ├── settings.html               Tabbed settings UI incl. Monitor tab; Danger Zone
│   └── static/
│       ├── app.js                  Shared JS: api(), formatPath(), openFile/Folder, inspectFile()
│       └── styles.css              Status badge colours
│
├── tests/
│   ├── rigs/
│   │   ├── README.md               Rig usage table and run-all commands
│   │   ├── check_db_migration.py   Layer 1 gate — DB schema and vault bootstrap (17 checks)
│   │   ├── check_settings_resolution.py  Layer 2 gate — 4-tier SettingsResolver (4 checks)
│   │   ├── check_resource_governor.py    Layer 2 gate — sensor + state machine (6 checks)
│   │   ├── check_worker_priority.py      Layer 2 gate — composite priority ordering (2 checks)
│   │   └── check_vault_api.py            Layer 3 gate — HTTP smoke tests (requires server)
│   └── unit/
│       ├── test_composite_priority.py    2 tests
│       ├── test_monitor.py               5 tests
│       ├── test_settings_resolver.py     5 tests
│       ├── test_vault_api.py             17 tests
│       └── test_vault_manager.py         6 tests
│
├── credentials/                    OAuth credentials (in .gitignore)
│   ├── google_credentials.json     Desktop app OAuth client secret
│   └── google_token.json           Saved OAuth token (created after first authorization)
│
└── docs/
    ├── plans/
    │   ├── 2026-02-26-docvault-design.md
    │   ├── 2026-02-26-docvault-implementation.md
    │   ├── 2026-03-02-future-architecture.md
    │   ├── 2026-03-03-infrastructure-migration-design.md
    │   └── 2026-03-03-infrastructure-migration-plan.md
    └── status/
        ├── 2026-02-28-project-status.md
        ├── 2026-03-01-project-status.md
        ├── 2026-03-02-project-status.md
        └── 2026-03-03-project-status.md   ← this file
```

---

## 6. Complete Settings Schema

| Key | Default | Group | Purpose |
|---|---|---|---|
| `paths:scan_directory` | `.` | General | Root folder to monitor for documents |
| `paths:cache_directory` | *(empty = app/.cache/extracted_images)* | General | Folder for extracted artefacts |
| `llm:provider` | `ollama` | General | LLM backend (`ollama` or `claude`) |
| `tesseract:path` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | General | Tesseract binary path |
| `ollama:host` | `http://localhost:11434` | Ollama | Ollama server URL |
| `ollama:chat_model` | `deepseek-r1:14b` | Ollama | Model for RAG chat |
| `ollama:embed_model` | `nomic-embed-text` | Ollama | Embedding model (change = full reindex required) |
| `ollama:num_ctx` | `8192` | Ollama | Context window (tokens) |
| `ollama:temperature` | `0.1` | Ollama | Sampling temperature |
| `ollama:num_predict` | `-1` | Ollama | Max tokens to generate (-1 = unlimited) |
| `ollama:top_p` | `0.9` | Ollama | Nucleus sampling threshold |
| `ollama:repeat_penalty` | `1.1` | Ollama | Repetition penalty |
| `qdrant:host` | `localhost` | Qdrant | Qdrant host |
| `qdrant:port` | `6333` | Qdrant | Qdrant port |
| `pdf:poppler_path` | *(empty)* | PDF | Poppler bin dir (required for pdf2image on Windows) |
| `pdf:sparse_threshold` | `50` | PDF | Chars below which a page is treated as scanned |
| `vision:model` | `minicpm-v` | Vision | Ollama vision model |
| `vision:describe_images` | `true` | Vision | Enable/disable vision description |
| `embeddings:chunk_size` | `600` | Embeddings | Chunk size in characters |
| `embeddings:chunk_overlap` | `100` | Embeddings | Overlap between chunks |
| `embeddings:score_threshold` | `0.65` | Embeddings | Min cosine similarity for search display |
| `search:rag_top_k` | `5` | Search | Chunks fed to LLM per RAG query |
| `search:rag_threshold` | `0.50` | Search | Min cosine similarity for RAG context |
| `search:result_limit` | `20` | Search | Max results per search query |
| `search:fts_weight` | `0.4` | Search | FTS weight in hybrid RRF merge |
| `search:sem_weight` | `0.6` | Search | Semantic weight in hybrid RRF merge |
| `video:describe_frames` | `true` | Video | Enable frame extraction + vision description |
| `video:frame_interval` | `10` | Video | Seconds between sampled frames |
| `google:credentials_path` | *(empty)* | Google | Path to OAuth client credentials JSON |
| `google:token_path` | *(empty)* | Google | Path where OAuth token is saved |
| `monitor:enabled` | `true` | Monitor | Enable/disable the resource governor entirely |
| `monitor:sample_interval` | `60` | Monitor | Seconds between hardware samples |
| `monitor:gpu_temp_throttle` | `80.0` | Monitor | GPU °C at which workers throttle |
| `monitor:gpu_temp_cooldown` | `88.0` | Monitor | GPU °C at which workers pause completely |
| `monitor:gpu_util_throttle` | `70.0` | Monitor | GPU util% at which workers throttle |
| `monitor:sustained_minutes` | `3` | Monitor | Consecutive throttle minutes before cooldown |
| `monitor:cooldown_minutes` | `10` | Monitor | Minutes workers pause during cooldown |
| `monitor:ram_throttle_pct` | `85.0` | Monitor | RAM% at which workers throttle |
| `monitor:cpu_temp_throttle` | `85.0` | Monitor | CPU °C at which workers throttle |

---

## 7. Key Architectural Decisions

### Hash-as-Primary-Key
Every document is identified by the SHA-256 hash of its content. The same file at a different name or location produces the same hash and the same DB record. Move detection is trivial; duplicate files are never re-embedded.

### 4-Tier Settings Lookup
`vault_settings` → settings.db global → `config.ini` → schema default. All four tiers resolved by `SettingsResolver(vault_id)`. The global `settings` singleton (3-tier) is preserved for backward compatibility — all existing call sites still work.

### Vault-as-Scan-Root
Each "vault" is a named scan root with its own priority, colour, state, and settings overrides. Workers iterate all active vaults on each ingestion cycle. Vault state machine enforces safe lifecycle transitions; source files are never touched.

### Composite Priority Formula
`((10 − vault_priority) × extractor_priority) + age_bonus` ensures that: (a) high-priority vault tasks are served first, (b) expensive/important file types beat cheap ones within a vault, and (c) tasks never starve indefinitely regardless of their vault's priority.

### Sensor Abstraction + Dummy Fallback
Hardware monitoring uses a sensor registry with a `DummySensor` fallback (returns 0s). Any machine that lacks an Nvidia GPU or WMI temperature access silently falls back to dummy sensors. The fallback is not persisted — re-tested on every startup to pick up newly available hardware.

### Strangler-Fig Extractor Migration
`LegacyExtractorAdapter` wraps the existing `(result, err)` extractor interface in the new `BaseExtractor` contract. Zero changes to extractor code. New extractors can implement `BaseExtractor` directly. The two styles coexist indefinitely.

### Three Databases
- `docvault.db` — document data; freely wipeable (wipe = re-index)
- `settings.db` — user configuration + vault records; survives data resets
- `logs.db` — observability (timings, errors, stats); can be cleared via Utilities → Clear Logs without affecting data or settings

### Daemon Thread Workers
Workers run as daemon threads for instant `Ctrl+C` exit. The pipeline is idempotent: interrupted tasks are reset to `PENDING` on restart.

---

## 8. Hardware & Runtime

| Component | Detail |
|---|---|
| Workstation | Windows 11, Ryzen 9 7950X3D, 128 GB RAM, RTX 4090 (24 GB VRAM) |
| Qdrant | Local Docker container |
| Ollama | Local workstation |
| Embeddings | `nomic-embed-text` via Ollama |
| OCR | Tesseract-OCR |
| PDF rendering | pdf2image + Poppler (`E:\DocVault\bin\poppler\Library\bin`) |
| Transcription | Whisper `large-v3`, CUDA fp16 |
| Vision | `minicpm-v` via Ollama |

---

## 9. Known Limitations & Future Work

| Item | Notes |
|---|---|
| Layer 3 HTTP rig | `tests/rigs/check_vault_api.py` requires server running; must be run manually before marking Layer 3 fully gated. |
| Search vault filtering backend | Vault IDs from the search UI are passed to endpoints but the search/semantic/FTS layers do not yet filter by vault — returns results from all vaults regardless. |
| Deleted file detection | Ingestor does not mark files as missing when they disappear from disk. |
| Claude API key | Read from `config.ini` only; not exposed in the Settings UI. |
| logs.db dashboard | `logs.db` is populated but there is no UI to browse it ("Nerds" diagnostics page is a planned follow-on). |
| Per-extractor configuration | No per-extractor settings (Whisper model, DPI, OCR language) in the UI. |
| Embedding model change | Changing `ollama:embed_model` invalidates all existing vectors; use Settings → Danger Zone → Rebuild Index. |
| 24/7 operation | Runs as a foreground process. No auto-restart or start-on-boot. |
| Security hardening | No API authentication — web UI is open to anyone who can reach the port. |
| GitHub release | Not yet packaged for public sharing. |
| Backup | No backup mechanism for the three datastores. |
