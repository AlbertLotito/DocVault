# Project Status — 2026-03-21

Supersedes all earlier status documents.

---

## 1. Embedding Progress Monitor — COMPLETE

Added real-time embedding progress display to eliminate false "pipeline stuck" alarms when large files are being embedded (e.g. a 1,747-chunk XML file taking 30 minutes).

### Changes

| File | Change |
|---|---|
| `api/routes/workers.py` | Extended `/api/workers/status` with `embedding_progress` object: filename, chunk X/Y, %, elapsed, ETA. Module-level `_embed_start` dict tracks start times per hash with per-hash eviction. |
| `frontend/static/lcars.js` | Workers sensor rail now shows `▶ 47% · 823/1747` instead of just `▶ RUNNING` when embedding is active. Priority: stalled > paused > embedding_progress > RUNNING. |
| `frontend/vault.html` | Added embedding progress card (hidden when idle) with filename, progress bar, chunk count, elapsed time, and ETA. Polls `/api/workers/status` every 5s independently from `lcars.js`. |

---

## 2. Fault-Tolerance Hardening — COMPLETE

Eliminated 10 brittle points identified in a full codebase audit. All changes are purely defensive — no new features, no new abstractions.

### Critical Fixes

| File | Problem | Fix |
|---|---|---|
| `workers/embedding_worker.py` | If Qdrant upsert failed AND SQLite was locked, the status-reset threw a second exception, suppressing the `raise`. Task stuck in EMBEDDING forever. | Wrapped `manager.update_task_status()` in its own `try/except` so `raise` is unconditional. |
| `run.py` | Watchdog restarted dead threads but never reset tasks the dead thread held in PROCESSING/EMBEDDING. Tasks orphaned until next full restart. | Added `manager.reset_stuck_tasks(DB_PATH)` call on every thread restart, wrapped in try/except so a DB error doesn't block the restart. |
| `run.py` | `thread.start()` called bare inside the watchdog. An OS thread-limit `RuntimeError` would kill the watchdog itself, stopping all future auto-recovery. | Wrapped thread construction and `.start()` in try/except; leaves dead entry in `live{}` for retry on next cycle. |
| `core/extractors/base.py` | `SubprocessExtractorAdapter` polled `process.poll()` then called `process.communicate()` after exit. Any kernel writing >64KB to stdout/stderr caused a classic OS pipe deadlock until the 300s kill timeout. | Background thread drains both pipes via `process.communicate()` continuously from process start; main thread polls `comm_thread.is_alive()` for cancel/timeout. |

### High Fixes

| File | Problem | Fix |
|---|---|---|
| `core/monitor.py` | `HardwareMonitor.run()` `while True:` loop had no exception handler. A sensor crash killed the thread permanently (not in watchdog's managed list). If in cooldown state, entire pipeline frozen for rest of uptime. | Loop body wrapped in `try/except Exception`; `time.sleep()` stays outside the try so errors never spin-loop. |
| `core/monitor.py` | `ollama_governor()` assigned the full `(state, reason)` tuple to `state`. Tuple is never equal to `'throttled'` or `'cooldown'` — thermal serialization lock **never applied**. | Fixed both occurrences to `state, _reason = get_throttle_state()`. |
| `core/ingestor.py` | Per-file exception handler caught only `(OSError, PermissionError)`. A `sqlite3.OperationalError` (DB lock during `insert_task`) bubbled up and terminated the entire vault directory scan. | Broadened to `except Exception`. Also wrapped unprotected folder-intelligence `manager.get_task()` / `manager.insert_task()` calls in try/except. |
| `workers/art_enrichment_worker.py` | `_write_nfo()` called inside except blocks with no protection. A `PermissionError` or full-disk error escaped, crashed the thread, watchdog restarted it, same image crashed it again — tight infinite loop. | Wrapped entire `for image_path in queue:` body in outer `try/except Exception as _loop_err` with `continue`. |

### Medium Fixes

| File | Problem | Fix |
|---|---|---|
| `core/manager.py` | `DELETE FROM fts_index` ran before the try/except protecting the chunking logic in `complete_extraction()`. A locked FTS table crashed the function before `conn.commit()`, rolling back the task status UPDATE and leaving a completed task stuck in PROCESSING. | Moved `DELETE FROM fts_index` inside the try/except. `conn.commit()` always runs; FTS is best-effort. |
| `embeddings/embedder.py` | `ollama.embeddings()` had no HTTP timeout. A stalled Ollama connection held a `ollama_governor()` semaphore slot indefinitely. With `max_parallel=1`, one hang = full pipeline freeze. | Uses `ollama.Client(timeout=N).embeddings()` where `N` is read from new `ollama:embed_timeout` setting (default 120s). |
| `core/settings.py` | New setting added: `ollama:embed_timeout` (int, default 120). |  |

### Low Fixes

| File | Problem | Fix |
|---|---|---|
| `workers/extraction_worker.py` | Nested exception handler logged `"could not mark task ERROR (will retry next run)"`. False — task was stuck in PROCESSING; claim query only picks up PENDING. Task was permanently lost, not retried. | Message now accurately states the task is stuck in PROCESSING and won't be retried until server restart. |
| `core/extractors/base.py` | `ExtractorLogger._write_log` and `_write_error` swallowed all DB exceptions with bare `pass`. If `logs.db` became corrupt, all pipeline visibility silently vanished. | Both methods now fall back to `print(..., file=sys.stderr)` on DB failure. |

---

## 3. Previous Work (still current)

### LCARS UI Overhaul (2026-03-15)

Full Warm LCARS dark design system across all pages. See 2026-03-15 status doc for complete page inventory and route changes.

### Qdrant Resilience (2026-03-14)

- `docker-compose.yml`: `restart: unless-stopped`
- Embedding worker: Qdrant failure resets to EXTRACTED (not ERROR); re-raise triggers reconnect
- Search degraded-mode: amber banner, FTS fallback when Qdrant offline

### Pipeline Fix (2026-03-15, commit 8552339)

`normalize()` silently dropped `meta` dict in 3 branches → fixed. `ocr_pages` now always written mid-flight for scanned PDF child task dispatch.

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
- Archive X-Ray: header-level search for ZIP/TAR
- Unified Settings: kernel-specific env vars into Settings UI
- "Nerds" diagnostics page (logs.db dashboard)
- Search enhancements: wildcard/regex in filename + content search

### Disk Sensor Status (partial)
MonitorReading ✓ | DiskSensor ✓ | `_merge_readings` ✓ | `_record_sample` ✓
ThrottleThresholds disk fields: **NOT YET** | StateMachine disk check: **NOT YET** | Settings schema: **NOT YET** | UI tile: **NOT YET**

---

## 5. Known Bugs

- **OCR charmap:** ~1,013 tasks in ERROR — `ocr_extractor.py` encodes to cp1252; fix → ensure unicode str throughout. Use `/api/utils/retry_extract_errors` after fix.
