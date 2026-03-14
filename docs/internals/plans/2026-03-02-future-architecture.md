# DocVault — Future Architecture Plan
**Date:** 2026-03-02
**Status:** Discussion — awaiting prioritisation
**Supersedes:** Nothing (new planning document)

---

## Overview

This document captures the full architectural design discussed on 2026-03-02 for the next major evolution of DocVault. Three large feature areas were designed:

1. **Multiple Vaults** — distinct, namespaced document collections with per-vault configuration
2. **Observability & Timing Logs** — separate `logs.db` with task timings, worker errors, and estimate engine
3. **Resource Governor** — autonomous CPU/GPU/RAM monitor with adaptive worker throttling

These are designs only. Implementation order to be decided separately.

---

## 1. Multiple Vaults

### Concept

A **vault** is a named, configured scan root. Rather than one global scan directory feeding one unified pool, each vault is a distinct namespace:

- `Documents` → `E:\My Docs`
- `Art` → `E:\Art Collection`
- `Scores` → `E:\Sheet Music`

Vaults are fully isolated by default but can be searched together. Each vault has its own identity, priority, LLM configuration, extraction heuristics, and prompts.

### MVC Principle

The vault management API defines a strong contract. The UX (tabs, modals, checkboxes) is a skin over that contract. Backend and frontend are independently changeable as long as the contract holds.

---

### 1.1 Database Schema

#### New `vaults` table (in `docvault.db`)

```sql
CREATE TABLE vaults (
    vault_id    TEXT PRIMARY KEY,   -- UUID
    name        TEXT NOT NULL,      -- "Documents", "Art", "Scores"
    scan_directory TEXT NOT NULL,
    priority    INTEGER DEFAULT 5,  -- lower = higher priority
    color       TEXT,               -- hex colour for UI differentiation
    created_at  TEXT,
    updated_at  TEXT
);
```

#### New `vault_settings` table (in `settings.db`)

```sql
CREATE TABLE vault_settings (
    vault_id    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (vault_id, key)
);
```

Same key/value pattern as global `settings` table, scoped to a vault.

#### `tasks` table gains `vault_id`

```sql
ALTER TABLE tasks ADD COLUMN vault_id TEXT REFERENCES vaults(vault_id);
```

Auto-migrated on startup (existing tasks assigned to a default vault if needed).

---

### 1.2 Settings Resolution Chain

Four-tier lookup, most specific wins:

```
vault_settings → settings.db (global) → config.ini → schema default
```

Extractors receive a `vault_id` context. The settings singleton becomes vault-aware: `settings.get(key, vault_id=None)`.

---

### 1.3 Per-Vault Configuration

Each vault exposes the full settings schema, with meaningful per-vault overrides:

| Setting Category | Example Per-Vault Use |
|---|---|
| LLM provider | Documents → Ollama, Art → Claude API (better vision reasoning) |
| Chat model | Different model per vault |
| Vision prompts | Art: "describe style, medium, subject, composition"; Scores: "transcribe text, key/time signatures"; Documents: generic |
| Extractor whitelist | MP3 vault skips PDF extraction entirely; Art vault skips Whisper |
| Extraction heuristics | `sparse_threshold`, `frame_interval`, `describe_images` per vault |
| Embeddings | `chunk_size`, `chunk_overlap`, `score_threshold` per vault |
| Rebuild index | Per-vault only — does not touch other vaults |

---

### 1.4 Qdrant — Single Collection, vault_id Payload Field

Every Qdrant point gets `vault_id` in its payload. Cross-vault search omits the filter; single-vault search uses `MatchAny([vault_id])`. Qdrant's HNSW indexing handles this efficiently.

**Scalability confirmed:** Qdrant handles 5–10M vectors (1M docs × 5–10 chunks) comfortably on available hardware. SQLite FTS5 handles 1M+ rows without concern. Single-collection approach is validated.

---

### 1.5 Worker Scheduling — Composite Priority with Aging

Workers are a **shared pool** across all vaults. Each task carries `vault_id` and inherits vault priority. No per-vault worker threads.

#### Priority Dimensions

1. **Vault priority** — user-configured integer (lower = higher priority)
2. **Extractor priority** — existing `PRIORITIES` dict in `router.py`
3. **Task age** — starvation prevention (aging bonus)

#### Effective Priority Formula

```
effective_priority = (vault_priority × extractor_priority) - age_bonus

age_bonus = (now - task_created_at).seconds / aging_rate_seconds
```

As a task ages its effective priority improves, guaranteeing eventual scheduling regardless of vault priority. `aging_rate_seconds` is configurable. This mirrors OS kernel fair scheduling (CFS-style).

#### Implementation

Replace the current `SELECT ... ORDER BY priority` query with an in-memory priority queue, re-scored on each worker poll cycle. Or: compute `effective_priority` as a SQL expression in the ORDER BY clause with a periodic UPDATE to `age_bonus`.

---

### 1.6 Search UI

- **Vault selector** — checkboxes at the top of the Search page (all tabs).
- Default selection configurable per vault in Settings (each vault has `search:default_selected = true/false`).
- Model is the source of truth for selected vaults; checkboxes reflect it.
- Adding/removing vaults or changing defaults requires no UI logic changes.

---

### 1.7 Vault API Contract

```
GET    /api/vaults                              List all vaults
POST   /api/vaults                              Create vault
GET    /api/vaults/{vault_id}                   Vault record + resolved settings
PUT    /api/vaults/{vault_id}                   Update vault metadata
DELETE /api/vaults/{vault_id}?mode=archive|gut|delete  (see §1.8)

GET    /api/vaults/{vault_id}/settings          Effective settings (resolved chain)
POST   /api/vaults/{vault_id}/settings          Set vault-level override
DELETE /api/vaults/{vault_id}/settings/{key}    Remove override (falls back to global)

POST   /api/vaults/{vault_id}/reindex           Wipe + requeue this vault only
POST   /api/vaults/{vault_id}/restore           Unarchive (Active ← Archived)
```

---

### 1.8 Vault Deletion — Progressive State Machine

Vault destruction is **state-enforced at the API level**, not just gated in the UI. Each stage must be completed before the next is offered. This creates a natural cooldown — you must return in a later session to proceed.

#### Vault State Machine

```
Active
  └─► Archived         (reversible — restore available)
        ├─► Active     (restore)
        └─► Gutted     (content wiped, definition remains — irreversible)
              └─► Deleted  (definition removed — irreversible)
```

The backend **rejects** a gut request unless `state == Archived`. Rejects a delete request unless `state == Gutted`. No UI trick can skip a step.

#### The Three Options

| Option | What Is Destroyed | Reversible? | When to Use |
|---|---|---|---|
| **A — Archive** | Nothing. Vault becomes inactive, scan stops. | Yes — restore instantly | Default "remove". Reorganising, temporarily suspending. |
| **B — Gut (Forget Content)** | Task records, FTS index, Qdrant vectors, cached images for this vault. Definition + settings kept. | No (but files on disk intact — re-processing possible) | Major extraction settings change. Clean restart of processing. |
| **C — Delete (Forget Everything)** | All of B, plus vault definition and settings. | No | Permanent removal. |

**Source files on disk are NEVER touched.** DocVault does not own them.

#### Destruction UX Flow

1. User clicks "Remove Vault" → presented with **Archive only** (recommended, reversible).
2. Vault must be Archived before "Forget Content" option appears.
3. Vault must be Gutted before "Delete" option appears.
4. Each step requires:
   - Clear explanation of what will be destroyed
   - Estimated re-processing cost (from timing logs — see §2)
   - Two-step confirmation (warning → bold red final)
   - "Delete" (Option C) requires typing the vault name to confirm (GitHub-style)

#### Estimated Re-processing Cost

Shown before each destructive action:

```
This vault contains:
  847 PDFs  (avg 12 pages, mix of text/scanned)
  234 audio files  (avg 4 min each)
  1,203 images

Estimated re-extraction:  ~6 hours
Estimated re-embedding:   ~40 minutes
Total:                    ~6.5 hours

Based on 3,241 previously processed files.
```

Confidence qualifier ("based on N files") reflects how reliable the estimate is.

---

### 1.9 UI — Naming and Navigation

#### Renamed Pages

| Old Name | New Name | Reason |
|---|---|---|
| Dashboard | Vault Status | Shows health/state of vaults, not a generic dashboard |
| Catalog | Vault Log | A log of processed files, not a library card catalog |

#### New Navigation Order

```
Search  →  Vault Status  →  Vault Log  →  Utilities  →  Settings
```

Most-used first; monitoring and admin pages descend naturally.

#### Vault Status Page

One card per vault showing:
- Name, scan directory
- File counts by status (Pending / Extracting / Extracted / Embedded / Error)
- Last scan time
- Current throttle state (from resource governor — see §3)
- Quick actions: Pause/Resume, Inspect, Archive

---

### 1.10 Settings UI — Multi-Level Tabs

```
Settings
├── Global          (Ollama host, Tesseract path, Qdrant, default vault selector)
└── Vaults
    ├── [+ New Vault]
    ├── Documents   ← vault tab
    │   ├── General     (name, directory, priority, colour)
    │   ├── LLM         (provider, model, prompts)
    │   ├── Extraction  (extractor whitelist, thresholds, frame interval)
    │   ├── Embeddings  (chunk size, overlap, score thresholds)
    │   └── Danger Zone (Archive → Gut → Delete, with estimates)
    ├── Art         ← vault tab
    └── Scores      ← vault tab
```

---

## 2. Observability — `logs.db`

### Concept

A third database, separate from `docvault.db` and `settings.db`, dedicated to observability data: task timings, worker errors, and pre-computed estimate statistics.

### Database Topology

| DB | Contains | Survives data reset? | Can be cleared independently? |
|---|---|---|---|
| `docvault.db` | tasks, FTS5, extracted images | No — IS the data | Yes (Danger Zone) |
| `settings.db` | configuration, pause state | Yes | No (would lose config) |
| `logs.db` | timings, errors, stats | Yes | Yes |

---

### 2.1 Schema

#### `task_timings`

```sql
CREATE TABLE task_timings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash       TEXT,
    vault_id        TEXT,
    extractor       TEXT,       -- "pdf_text", "pdf_ocr", "pdf_vision", "whisper", "vision", "embed"
    file_size       INTEGER,    -- bytes
    page_count      INTEGER,    -- PDFs
    duration_secs   REAL,       -- media duration (audio/video)
    elapsed_secs    REAL,       -- actual wall-clock time taken
    completed_at    TEXT        -- ISO8601
);
```

#### `worker_errors`

```sql
CREATE TABLE worker_errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash       TEXT,
    vault_id        TEXT,
    extractor       TEXT,
    error_type      TEXT,
    error_message   TEXT,
    traceback       TEXT,
    occurred_at     TEXT
);
```

#### `extractor_stats` (materialised rolling averages)

```sql
CREATE TABLE extractor_stats (
    vault_id        TEXT,           -- NULL = global aggregate
    extractor       TEXT,
    sample_count    INTEGER,
    avg_secs        REAL,
    p50_secs        REAL,
    p95_secs        REAL,
    last_updated    TEXT,
    PRIMARY KEY (vault_id, extractor)
);
```

`extractor_stats` is recomputed periodically from `task_timings`. Estimate queries are simple lookups, not aggregate scans.

---

### 2.2 Estimate Engine

When a destructive vault operation is about to queue N files:

1. Look up vault's file composition from `tasks` table (count by type)
2. Look up `extractor_stats` for each extractor × file type
3. Sum expected time, add embedding estimate
4. Present to user with confidence qualifier

Self-calibrating: estimates improve as more files are processed. Hardware-specific: timings reflect the actual machine, not generic benchmarks.

---

### 2.3 Dashboard / Vault Status Uses

- "N files pending — estimated completion in 3h 20m"
- Per-vault progress with time remaining
- Extractor throughput over time (files/hour)
- Worker error history (structured, queryable — not just print() statements)

---

### 2.4 `logs.db` Lifecycle

- Created and initialised on startup alongside `settings.db` and `docvault.db`
- Can be cleared from Utilities page without affecting operations
- Can be exported as a diagnostics artifact (no document content — safe to share)
- Should be included in any future backup scheme

---

## 3. Resource Governor

### Concept

An autonomous background daemon (`core/monitor.py`) that samples hardware metrics once per minute and adaptively throttles the worker pool based on resource pressure. DocVault backs off when the machine is busy (gaming, 4K editing, rendering) and resumes when resources are available — without any user intervention.

---

### 3.1 What Is Sampled

| Metric | Source |
|---|---|
| CPU utilization % | `psutil` |
| CPU temperature | `wmi` (Windows; `psutil` insufficient on Win) |
| RAM used / total | `psutil` |
| GPU temperature | `pynvml` (nvidia-ml-py3) |
| GPU utilization % | `pynvml` |
| VRAM used / total | `pynvml` |

All sampled once per `monitor:sample_interval` (default 60 seconds). Negligible overhead.

---

### 3.2 Throttle State Machine

```
Normal
  └─► Throttled      (resource pressure detected)
        ├─► Normal   (pressure clears within cooldown window)
        └─► Cooldown (sustained pressure for monitor:sustained_minutes)
              └─► Retest after monitor:cooldown_minutes
                    ├─► Normal   (pressure gone)
                    └─► Cooldown (still busy — wait again)
```

#### Throttle Triggers

| Condition | Action |
|---|---|
| GPU temp > `monitor:gpu_temp_throttle` | → Throttled |
| GPU temp > `monitor:gpu_temp_cooldown` | → Cooldown immediately |
| GPU util > `monitor:gpu_util_throttle` sustained for `monitor:sustained_minutes` | → Cooldown |
| RAM > `monitor:ram_throttle_pct` | → Throttled |
| CPU temp > `monitor:cpu_temp_throttle` | → Throttled |
| All metrics clear after cooldown wait | → Normal |

The GPU utilization sustained trigger is the key one: it catches gaming, 4K editing, or any heavy GPU workload and backs DocVault off automatically.

#### Throttle Effect on Workers

| State | Worker Behaviour |
|---|---|
| Normal | Full worker count, normal polling interval |
| Throttled | Half worker count, inter-task sleep added |
| Cooldown | One worker (or full pause), long polling interval |

Implemented via the existing pause/throttle shared state in `workers/utils.py`, driven programmatically by the governor instead of the user.

---

### 3.3 Settings

```
monitor:enabled              true
monitor:sample_interval      60        seconds between samples
monitor:gpu_temp_throttle    80        °C — enter Throttled
monitor:gpu_temp_cooldown    88        °C — enter Cooldown immediately
monitor:gpu_util_throttle    70        % GPU utilization
monitor:sustained_minutes    3         minutes of sustained pressure before Cooldown
monitor:cooldown_minutes     10        minutes before retest
monitor:ram_throttle_pct     85        % RAM usage threshold
monitor:cpu_temp_throttle    85        °C
```

All configurable via Settings → Global.

---

### 3.4 `system_stats` Table (in `logs.db`)

```sql
CREATE TABLE system_stats (
    sampled_at      TEXT PRIMARY KEY,
    cpu_pct         REAL,
    cpu_temp        REAL,
    ram_used_gb     REAL,
    ram_total_gb    REAL,
    ram_pct         REAL,
    gpu_temp        REAL,
    gpu_util_pct    REAL,
    vram_used_gb    REAL,
    vram_total_gb   REAL,
    throttle_state  TEXT    -- "normal", "throttled", "cooldown"
);
```

Storing `throttle_state` with each sample allows post-hoc correlation: "GPU hit 83°C at 14:32, throttle engaged, temps dropped to 71°C by 14:38."

---

### 3.5 Vault Status UI — Resource Strip

A persistent strip at the top of the Vault Status page:

```
CPU 34°C  42%  |  RAM 28 GB / 128 GB  |  GPU 61°C  12%  ●  Normal
```

- Colour-coded state dot: green (Normal) / yellow (Throttled) / red (Cooldown)
- Human-readable note if throttled: "GPU utilization high — workers throttled"
- No graphs in v1 — current state only; historical data available in `logs.db` for later

---

## 4. Summary of New Components

### New Files

| File | Purpose |
|---|---|
| `core/monitor.py` | Resource sampler + governor, daemon thread |
| `core/vault_manager.py` | Vault CRUD, vault_settings resolution, state machine |
| `api/routes/vaults.py` | Vault API endpoints |

### Modified Files

| File | Change |
|---|---|
| `core/manager.py` | `vault_id` on tasks; vault + vault_settings tables |
| `core/settings.py` | Vault-aware 4-tier resolution chain |
| `core/ingestor.py` | Per-vault scan directories, vault context |
| `workers/utils.py` | Throttle level shared state (Normal/Throttled/Cooldown) |
| `workers/extraction_worker.py` | Composite priority queue with aging |
| `workers/embedding_worker.py` | Per-vault embedding settings |
| `run.py` | Init logs.db; start monitor daemon thread |
| `api/main.py` | Mount vaults router |
| `frontend/search.html` | Vault selector checkboxes |
| `frontend/index.html` | Rename to Vault Status; resource strip; per-vault cards |
| `frontend/catalog.html` | Rename to Vault Log; vault filter |
| `frontend/settings.html` | Multi-level tabs (Global + per-vault) |

### New Database

| DB | New Tables |
|---|---|
| `logs.db` | `task_timings`, `worker_errors`, `extractor_stats`, `system_stats` |

### Modified Databases

| DB | Change |
|---|---|
| `docvault.db` | New `vaults` table; `vault_id` column on `tasks` |
| `settings.db` | New `vault_settings` table |

---

## 5. Known Open Questions

| Question | Notes |
|---|---|
| Vault directory overlap | Should two vaults be allowed to watch overlapping directories? Probably not — guard at vault creation time. |
| Default vault migration | Existing tasks have no `vault_id`. On migration, assign them to a "Default" vault created from current `paths:scan_directory`. |
| Vault colour scheme | Suggest 6–8 preset colours in the UI; user picks from palette rather than free hex input. |
| `logs.db` retention | How many rows before `system_stats` and `task_timings` are auto-pruned? Configurable, default maybe 90 days. |
| Per-vault worker count | Future: allow vault to specify max concurrent workers (not just priority). Not in scope for this iteration. |
| GPU monitoring non-NVIDIA | `pynvml` is NVIDIA-only. AMD/Intel GPU support would need a separate path. Not in scope — this is an NVIDIA machine. |
