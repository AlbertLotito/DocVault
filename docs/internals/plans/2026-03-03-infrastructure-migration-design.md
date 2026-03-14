# DocVault — Infrastructure Migration Design
**Date:** 2026-03-03
**Status:** Approved — ready for implementation planning
**Supersedes:** Sections 1–3 of `2026-03-02-future-architecture.md` (which remains the reference for UI detail)

---

## Overview

This document specifies the full design for the DocVault infrastructure migration. The goal is to lay the correct foundations — data stores, contracts, scheduling, observability, and resource management — before building any new end-user features. Retrofitting these concerns later would be significantly more expensive.

The migration is delivered in four **layered increments**, each independently testable before the next begins.

---

## Core Insight: DocVault Is a Document Processing Runtime

DocVault is not just an indexer. It is a **document processing runtime**:

- Vaults are namespaces (like OS mount points)
- The worker pool is a scheduler (CFS-style priority aging)
- Extractors are drivers (abstract over tools, AI models, remote APIs)
- The Resource Governor is a thermal/power management daemon
- `ExtractorContext` is the process execution environment
- `IngestResult` is the syscall return convention
- `logs.db` is the kernel ring buffer

This mental model justifies building contracts and infrastructure first, before features. OS designs are reliable precisely because they enforce separation of concerns ruthlessly.

---

## Delivery Plan — Four Layers

| Layer | Contents | Gate |
|---|---|---|
| **1** | DB schema foundation, auto-migration, logs.db | `check_db_migration.py` passes |
| **2** | Extractor contracts, settings resolution, worker scheduling, resource governor | `check_settings_resolution.py`, `check_resource_governor.py`, `check_worker_priority.py` pass |
| **3** | Vault API, VaultManager, state machine | `check_vault_api.py` passes |
| **4** | Full UI — Vault Status, Vault Log, vault selector, per-vault settings, resource strip | Manual + automated UI validation |

Each layer is committed to master before the next begins.

---

## Layer 1 — DB / Schema Foundation

### `docvault.db` changes

#### New `vaults` table

```sql
CREATE TABLE vaults (
    vault_id       TEXT PRIMARY KEY,          -- UUID4
    name           TEXT NOT NULL,
    scan_directory TEXT NOT NULL,
    priority       INTEGER DEFAULT 5,         -- lower = higher priority
    color          TEXT DEFAULT '#6366f1',    -- indigo; chosen from preset palette
    state          TEXT DEFAULT 'active',     -- active | archived | gutted | deleted
    created_at     TEXT,                      -- ISO8601
    updated_at     TEXT                       -- ISO8601
);
```

#### `tasks` table — new `vault_id` column

```sql
ALTER TABLE tasks ADD COLUMN vault_id TEXT REFERENCES vaults(vault_id);
```

Added via `ALTER TABLE` on startup. Nullable so migration is non-destructive; immediately backfilled.

---

### `settings.db` changes

#### New `vault_settings` table

```sql
CREATE TABLE vault_settings (
    vault_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (vault_id, key)
);
```

Same key/value pattern as the global `settings` table, scoped to a vault.

---

### `logs.db` — new file

Third database alongside `docvault.db` and `settings.db`. Created on startup if absent. Contains observability data only — no document content, safe to clear or share.

| DB | Survives data reset? | Can be cleared independently? |
|---|---|---|
| `docvault.db` | No — IS the data | Yes (Danger Zone) |
| `settings.db` | Yes | No (would lose config) |
| `logs.db` | Yes | Yes (Utilities page) |

#### `task_timings`

```sql
CREATE TABLE task_timings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash     TEXT,
    vault_id      TEXT,
    extractor     TEXT,         -- "pdf_text", "pdf_ocr", "whisper", "vision", "embed", etc.
    file_size     INTEGER,      -- bytes
    page_count    INTEGER,      -- PDFs only
    duration_secs REAL,         -- media duration (audio/video)
    elapsed_secs  REAL,         -- actual wall-clock time
    completed_at  TEXT          -- ISO8601
);
```

#### `worker_errors`

```sql
CREATE TABLE worker_errors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash     TEXT,
    vault_id      TEXT,
    extractor     TEXT,
    error_type    TEXT,
    error_message TEXT,
    traceback     TEXT,
    occurred_at   TEXT          -- ISO8601
);
```

#### `worker_log`

```sql
CREATE TABLE worker_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash     TEXT,
    vault_id      TEXT,
    extractor     TEXT,
    level         TEXT,         -- DEBUG | INFO | WARNING | ERROR | CRITICAL
    message       TEXT,
    occurred_at   TEXT          -- ISO8601
);
```

DEBUG and INFO rows only written when debug logging is enabled (configurable per vault).

#### `extractor_stats` (materialised rolling averages)

```sql
CREATE TABLE extractor_stats (
    vault_id      TEXT,         -- NULL = global aggregate
    extractor     TEXT,
    sample_count  INTEGER,
    avg_secs      REAL,
    p50_secs      REAL,
    p95_secs      REAL,
    last_updated  TEXT,
    PRIMARY KEY (vault_id, extractor)
);
```

Recomputed periodically from `task_timings`. Estimate queries are simple lookups, not aggregate scans.

#### `system_stats`

```sql
CREATE TABLE system_stats (
    sampled_at    TEXT PRIMARY KEY,   -- ISO8601
    cpu_pct       REAL,
    cpu_temp      REAL,
    ram_used_gb   REAL,
    ram_total_gb  REAL,
    ram_pct       REAL,
    gpu_temp      REAL,
    gpu_util_pct  REAL,
    vram_used_gb  REAL,
    vram_total_gb REAL,
    throttle_state TEXT              -- normal | throttled | cooldown
);
```

---

### Auto-migration logic (`core/manager.py`)

Startup sequence (idempotent — safe on every restart):

1. `init_logs_db()` — creates `logs.db` and all four tables if absent
2. `init_settings_db()` — creates `vault_settings` table if absent (existing `settings` table untouched)
3. `init_db()` — creates `vaults` table if absent; adds `vault_id` column to `tasks` if absent
4. **Default vault bootstrap** (runs if `vaults` table is empty):
   - Creates vault named `"Documents"` with `vault_id = UUID4`, `scan_directory = settings.get('paths:scan_directory')`
   - `UPDATE tasks SET vault_id = <new_vault_id> WHERE vault_id IS NULL`

Migration is deterministic and non-destructive. No data loss risk. Existing tasks and settings are preserved.

---

### Layer 1 test rig

`tests/rigs/check_db_migration.py --help`

Validates:
- `vaults` table exists with correct schema
- `vault_id` column present on `tasks`
- `vault_settings` table exists in `settings.db`
- `logs.db` exists with all four tables
- At least one vault exists (the Documents default)
- All tasks have a non-null `vault_id`

---

## Layer 2 — Extractor Contracts, Settings, Worker Scheduling, Resource Governor

### 2a. Extractor Contract System

#### The fundamental insight

Each extractor type produces rich, type-specific output. The infrastructure (DB, FTS5, Qdrant, logs) needs a uniform envelope. A normalisation layer sits between them.

```
Extractor.extract()  →  ExtractorResult  (rich, type-specific)
                               ↓
                        .normalize()      (on the base class)
                               ↓
                        IngestResult      (universal envelope)
                               ↓
                        IngestHandler     (writes DB, FTS5, queues embedding)
```

The worker calls `.run()` only. It never sees `ExtractorResult`.

---

#### `ExtractorContext` — execution environment

All per-invocation context travels in one object, passed to `run()` and all methods:

```python
@dataclass
class ExtractorContext:
    vault_id:     str
    file_hash:    str
    cancel_token: threading.Event    # kill signal
    logger:       ExtractorLogger    # structured logger → logs.db
    settings:     SettingsResolver   # vault-aware 4-tier lookup
    timeout_secs: int | None         # auto-cancel after N seconds (None = no limit)
```

Analogue: OS process execution environment. The extractor doesn't manage its own lifecycle — the worker does.

---

#### `IngestResult` — universal envelope

```python
@dataclass
class IngestResult:
    text:           str | None                  # main body → FTS5 + embedding
    metadata:       dict                        # pages, duration, language, etc.
    images:         list[ExtractedImage]        # embedded images → cache
    child_tasks:    list[ChildTask]             # Archive type: new queue entries
    enrichments:    list[Enrichment]            # Synthesis type: tags, summaries, entities
    errors:         list[ExtractError]          # partial failures (non-blocking)
    status:         str                         # success | partial | failed | cancelled
    extractor_name: str
    elapsed_secs:   float
```

`cancelled` status: task resets to `PENDING` (not `ERROR`) — retryable. Partial text accumulated before cancellation is preserved.

---

#### `BaseExtractor` — the contract

```python
class BaseExtractor(ABC):

    def run(self, file_path: Path, ctx: ExtractorContext) -> IngestResult:
        """Called by worker. Enforces timeout, logs elapsed time to logs.db."""
        ...

    @abstractmethod
    def extract(self, file_path: Path, ctx: ExtractorContext) -> ExtractorResult:
        """Extractor implements this. Must check ctx.cancel_token at safe checkpoints."""
        ...

    @abstractmethod
    def normalize(self, result: ExtractorResult, ctx: ExtractorContext) -> IngestResult:
        """Converts type-specific result to universal IngestResult."""
        ...
```

---

#### Extractor type hierarchy

Each type has its own base class extending `BaseExtractor` with type-appropriate properties:

| Type | Base Class | Key Properties | Examples |
|---|---|---|---|
| **Utility** | `UtilityExtractor` | `tool_path`, `timeout` | Tesseract, Poppler, ffmpeg, pypdf, python-docx |
| **Intelligent** | `IntelligentExtractor` | `model`, `provider`, `temperature`, `system_prompt` | Ollama vision, Whisper, future Gemini/Claude vision |
| **Chat** | `ChatExtractor` (extends Intelligent) | `history: list[Message]` | RAG query, document Q&A |
| **Pipeline** | `PipelineExtractor` | `stages: list[BaseExtractor]`, `stop_condition` | PDF multi-stage, future image pipeline |
| **Remote** | `RemoteExtractor` | `credentials`, `endpoint`, `retry_policy`, `rate_limit` | Google Drive, future SharePoint/Dropbox |
| **Archive** | `ArchiveExtractor` | `max_depth`, `child_router` | ZIP, tar, .eml/.msg with attachments |
| **Synthesis** | `SynthesisExtractor` | `prompt_template` | Auto-summary, auto-tagging, entity extraction |

Future extractor types (Streaming, Structured, Federated) fit cleanly into this hierarchy without changing `BaseExtractor`.

---

#### `ExtractorLogger` — structured logging

Thin wrapper around `logs.db`. Not Python's `logging` module.

| Level | Written to | When |
|---|---|---|
| `DEBUG` | `worker_log` (if debug mode on) | Every step — page N done, chunk Y embedded, raw tool output |
| `INFO` | `worker_log` | Start, finish, stage transitions |
| `WARNING` | `worker_log` + `worker_errors` | Non-fatal — fallback triggered, partial extraction |
| `ERROR` | `worker_errors` | Stage failed, partial result |
| `CRITICAL` | `worker_errors` | Total failure, no output produced |

DEBUG/INFO written only when `extractor:debug_logging = true` (configurable per vault). Off by default. All levels available immediately — no restart required to enable.

---

#### Kill signal — cooperative cancellation

`ctx.cancel_token` is a `threading.Event`. Extractors check it at safe checkpoints (between pages, frames, audio segments). On set:

```python
for page in pages:
    if ctx.cancel_token.is_set():
        ctx.logger.warning(f"Cancelled after {i}/{total} pages")
        break
    # ... process page
```

Three sources can set the token:

| Source | Trigger |
|---|---|
| **Timeout** | `ctx.timeout_secs` exceeded — fired automatically by worker wrapper |
| **Resource Governor** | Throttle → Cooldown, task is a known long-runner |
| **User** | "Kill task" button on Vault Status page → API → shared cancel registry |

Per-extractor-type timeout defaults (configurable per vault in settings):

```
extractor:whisper_timeout_secs      3600   # 1 hour
extractor:vision_timeout_secs        300   # 5 min per image
extractor:pdf_ocr_timeout_secs      1800   # 30 min for a large scanned book
extractor:default_timeout_secs       600   # 10 min fallback for all others
```

---

### 2b. Settings Resolution Chain

Four-tier lookup, most specific wins:

```
vault_settings  →  settings.db (global)  →  config.ini  →  schema default
```

`settings.get(key, vault_id=None)` — vault_id=None falls through to global chain only. All extractors receive settings via `ctx.settings`, never at import time.

`SettingsResolver` is a lightweight object constructed per-invocation from vault_id. No global singleton mutation needed.

---

### 2c. Worker Scheduling — Composite Priority with Aging

Replace `SELECT ... ORDER BY priority` with a composite priority expression:

```
effective_priority = (vault_priority × extractor_priority) − age_bonus

age_bonus = elapsed_seconds_since_created / aging_rate_seconds
```

`aging_rate_seconds` configurable (default: 3600). As a task ages, its effective priority improves, guaranteeing eventual scheduling regardless of vault priority. Mirrors Linux CFS.

Implementation: SQL `ORDER BY` with computed expression. No in-memory priority queue needed for v1.

---

### 2d. Resource Governor (`core/monitor.py`)

Daemon thread. Samples hardware once per `monitor:sample_interval` (default 60s). Negligible overhead.

#### Sensor abstraction layer

The governor does not call `pynvml` or `wmi` directly. It uses a sensor registry:

```
HardwareMonitor
    SensorRegistry
        ├── CPUSensor (abstract)
        │   ├── PSUtilCPUSensor       (psutil — utilization, cross-platform)
        │   ├── WMICPUTempSensor      (wmi — Windows CPU temperature)
        │   └── DummyCPUSensor        (fallback — returns zeros, warns on init)
        ├── RAMSensor
        │   ├── PSUtilRAMSensor
        │   └── DummyRAMSensor
        └── GPUSensor (abstract)
            ├── NvidiaSensor          (pynvml — NVIDIA)
            ├── (future) AMDSensor    (future — ROCm/ADL)
            ├── (future) IntelSensor  (future — oneAPI)
            └── DummyGPUSensor        (fallback)
```

On startup, each sensor attempts initialisation. Failure → warning logged → `DummySensor` substituted. The fallback is **not persisted** — on next startup, real sensor init is always retried. This design accommodates future GPU vendors without changing the governor.

#### Throttle state machine

```
Normal
  └─► Throttled      (resource pressure detected)
        ├─► Normal   (pressure clears)
        └─► Cooldown (sustained pressure for monitor:sustained_minutes)
              └─► Retest after monitor:cooldown_minutes
                    ├─► Normal
                    └─► Cooldown
```

#### Throttle triggers

| Condition | Action |
|---|---|
| GPU temp > `monitor:gpu_temp_throttle` (80°C) | → Throttled |
| GPU temp > `monitor:gpu_temp_cooldown` (88°C) | → Cooldown immediately |
| GPU util > `monitor:gpu_util_throttle` (70%) sustained for `monitor:sustained_minutes` (3 min) | → Cooldown |
| RAM > `monitor:ram_throttle_pct` (85%) | → Throttled |
| CPU temp > `monitor:cpu_temp_throttle` (85°C) | → Throttled |

#### Throttle effect on workers

| State | Worker behaviour |
|---|---|
| Normal | Full worker count, normal polling |
| Throttled | Half worker count, inter-task sleep |
| Cooldown | One worker (or full pause), long polling; cancel tokens set on active long-runners |

All thresholds configurable in Settings → Global.

---

### Layer 2 test rigs

`tests/rigs/check_settings_resolution.py`
- Verifies each tier in the 4-tier chain overrides correctly
- Vault-scoped overrides do not leak to other vaults

`tests/rigs/check_resource_governor.py`
- Each sensor probe inits or falls back to dummy with a warning
- Dummy sensor returns zeros, does not raise
- Throttle state transitions fire at correct thresholds (uses injected mock metrics)
- State machine rejects invalid transitions

`tests/rigs/check_worker_priority.py`
- Composite priority ordering verified against known inputs
- Age bonus increases over time
- Tasks in low-priority vault eventually scheduled (anti-starvation)

---

## Layer 3 — Vault API and VaultManager

### `core/vault_manager.py`

Handles vault CRUD, settings resolution, state machine transitions, per-vault reindex, and estimated re-processing cost.

Estimate engine: looks up vault file composition from `tasks`, looks up `extractor_stats` for each extractor × file type, sums expected time. Confidence qualifier reflects sample count.

### API contract (`api/routes/vaults.py`)

```
GET    /api/vaults                                List all vaults
POST   /api/vaults                                Create vault
GET    /api/vaults/{vault_id}                     Vault record + resolved settings
PUT    /api/vaults/{vault_id}                     Update vault metadata
DELETE /api/vaults/{vault_id}?mode=archive        Transition: Active → Archived (reversible)
DELETE /api/vaults/{vault_id}?mode=gut            Transition: Archived → Gutted (irreversible)
DELETE /api/vaults/{vault_id}?mode=delete         Transition: Gutted → Deleted (irreversible)

GET    /api/vaults/{vault_id}/settings            Effective settings (resolved chain)
POST   /api/vaults/{vault_id}/settings            Set vault-level override
DELETE /api/vaults/{vault_id}/settings/{key}      Remove override (falls back to global)

POST   /api/vaults/{vault_id}/reindex             Wipe + requeue this vault only
POST   /api/vaults/{vault_id}/restore             Archived → Active
```

#### Vault state machine (enforced at API level, not just UI)

```
Active
  └─► Archived        (reversible — restore available)
        ├─► Active    (restore)
        └─► Gutted    (content wiped — irreversible)
              └─► Deleted  (definition removed — irreversible)
```

Backend **rejects** gut request unless `state == archived`. Rejects delete unless `state == gutted`. No UI trick can skip a step.

| State | What exists | Scan active? |
|---|---|---|
| Active | Everything | Yes |
| Archived | Everything | No |
| Gutted | Vault definition + settings only | No |
| Deleted | Nothing | No |

Source files on disk are **never touched**.

---

### Layer 3 test rig

`tests/rigs/check_vault_api.py`
- Full CRUD over HTTP
- State machine: valid transitions succeed, invalid transitions return 409
- Per-vault settings: override, resolution, deletion
- Reindex: only affects target vault's tasks
- Estimate response includes file counts and time estimate
- Vault directory overlap guard: creating vault with duplicate scan_directory returns 409

---

## Layer 4 — Full UI

### Page renames and navigation

| Old | New | Reason |
|---|---|---|
| Dashboard | Vault Status | Shows health/state of each vault |
| Catalog | Vault Log | A log of processed files, not a library catalog |

New navigation order:
```
Search  →  Vault Status  →  Vault Log  →  Utilities  →  Settings
```

### Vault Status page

One card per vault:
- Name, scan directory, state badge
- File counts: Pending / Extracting / Extracted / Embedded / Error
- Last scan time
- Quick actions: Pause/Resume, Archive
- Resource strip (see below)

**Resource strip** (top of page):
```
CPU 34°C  42%  |  RAM 28 GB / 128 GB  |  GPU 61°C  12%  ●  Normal
```
- Colour-coded dot: green (Normal) / amber (Throttled) / red (Cooldown)
- Human-readable note when throttled: "GPU utilization high — workers throttled"
- Polls `GET /api/monitor/status` every 10s

### Search page

Vault selector checkboxes above all three tabs. All vaults selected by default. Default selection configurable per vault in settings.

### Settings page — multi-level tabs

```
Settings
├── Global          (Ollama host, Tesseract path, Qdrant, monitor thresholds)
└── Vaults
    ├── [+ New Vault]
    ├── Documents
    │   ├── General     (name, directory, priority, colour picker)
    │   ├── LLM         (provider, model, prompts)
    │   ├── Extraction  (extractor whitelist, thresholds, timeouts)
    │   ├── Embeddings  (chunk size, overlap, score thresholds)
    │   └── Danger Zone (Archive → Gut → Delete with estimates)
    └── (additional vaults...)
```

Vault Danger Zone shows destruction estimate before each step:
```
This vault contains:
  847 PDFs  (avg 12 pages)
  234 audio files  (avg 4 min)
  1,203 images

Estimated re-extraction:  ~6 hours
Estimated re-embedding:   ~40 minutes
Based on 3,241 previously processed files.
```

Delete (Option C) requires typing the vault name to confirm (GitHub-style).

---

## Test Rig Framework

### Location: `tests/rigs/`

All rigs are standalone Python scripts. Common conventions:
- `argparse` with `--help` describing purpose, inputs, and exit codes
- Exit 0 = pass, Exit 1 = fail (composable into CI or a master runner)
- `--verbose` flag for full diagnostic output
- `--db`, `--settings-db`, `--logs-db` flags to override default paths (safe to run against a copy)
- Coloured console output: green PASS / red FAIL / yellow WARN

### Full rig inventory

| Script | Layer | Validates |
|---|---|---|
| `check_db_migration.py` | 1 | vaults table, vault_id on tasks, Documents vault, logs.db tables |
| `check_settings_resolution.py` | 2 | 4-tier chain, vault scoping, override/fallback behaviour |
| `check_resource_governor.py` | 2 | Sensor init/fallback, throttle state transitions (mock metrics) |
| `check_worker_priority.py` | 2 | Composite priority ordering, aging, anti-starvation |
| `check_vault_api.py` | 3 | Full CRUD, state machine, per-vault settings, reindex isolation |

`tests/rigs/README.md` documents all rigs, purpose, common invocations, and expected output.

---

## New and Modified Files

### New files

| File | Purpose |
|---|---|
| `core/monitor.py` | Resource governor daemon + sensor registry |
| `core/vault_manager.py` | Vault CRUD, state machine, estimate engine |
| `core/extractors/base.py` | `BaseExtractor`, `ExtractorContext`, `IngestResult`, type hierarchy bases |
| `api/routes/vaults.py` | Vault API endpoints |
| `api/routes/monitor.py` | `GET /api/monitor/status` for resource strip |
| `tests/rigs/check_db_migration.py` | Layer 1 test rig |
| `tests/rigs/check_settings_resolution.py` | Layer 2 test rig |
| `tests/rigs/check_resource_governor.py` | Layer 2 test rig |
| `tests/rigs/check_worker_priority.py` | Layer 2 test rig |
| `tests/rigs/check_vault_api.py` | Layer 3 test rig |
| `tests/rigs/README.md` | Rig documentation |

### Modified files

| File | Change |
|---|---|
| `core/manager.py` | `vaults` table, `vault_id` on tasks, `logs.db` init, auto-migration |
| `core/settings.py` | 4-tier vault-aware resolution; `SettingsResolver` per-invocation object |
| `core/ingestor.py` | Per-vault scan directories; vault context passed to workers |
| `workers/utils.py` | Throttle state enum (Normal/Throttled/Cooldown); cancel token registry |
| `workers/extraction_worker.py` | `ExtractorContext` construction; composite priority ORDER BY |
| `workers/embedding_worker.py` | Per-vault embedding settings via `SettingsResolver` |
| `run.py` | `init_logs_db()`; start monitor daemon thread |
| `api/main.py` | Mount vaults + monitor routers |
| `frontend/index.html` | Rename → Vault Status; resource strip; per-vault cards |
| `frontend/catalog.html` | Rename → Vault Log; vault filter column |
| `frontend/search.html` | Vault selector checkboxes |
| `frontend/settings.html` | Multi-level tabs (Global + per-vault) |

---

## Future Work Noted (Not In Scope)

- **"Nerds" diagnostics page** — Utilities tab addition. Exposes system_stats, worker_log, extractor_stats, throttle history, sensor readings in a live dashboard. All data exists in logs.db by the time this is built — it is a pure display layer.
- **New vault wizard** — guided setup flow for creating additional vaults (name, directory, colour, LLM, extraction settings). Current scope: create via Settings → Vaults → [+ New Vault] form only.
- **Streaming extractors** — mixin for large files (multi-GB video, large audio) processed in chunks without full memory load.
- **Structured extractors** — schema-aware parsing (CSV as table, JSON as graph, SQL dumps).
- **Archive extractors** — ZIP, tar, .eml/.msg with attachments; yields child tasks back into queue.
- **Synthesis extractors** — post-extraction enrichment (auto-summary, tagging, NER).
- **Non-NVIDIA GPU sensors** — AMD (ROCm/ADL), Intel (oneAPI). Sensor abstraction layer accommodates these without changing the governor.
- **Per-vault worker count** — max concurrent workers per vault (beyond priority).
- **`logs.db` retention policy** — auto-prune `system_stats` and `task_timings` after configurable retention period (default 90 days).
