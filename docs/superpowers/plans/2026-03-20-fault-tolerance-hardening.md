# Fault-Tolerance Hardening Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate 10 identified brittle points where silent failures can freeze the pipeline, orphan tasks, kill daemon threads, or corrupt state.

**Architecture:** All changes are defensive wrappers and guard clauses — no new abstractions, no new files. Each fix is surgical: add a try/except, move a line inside a guard, fix a wrong assignment. Zero new features.

**Tech Stack:** Python 3.11+, SQLite (sqlite3), threading, subprocess, ollama-python, FastAPI

**Audit source:** `C:\Users\Albert\.claude\temp\analysis.md`

---

## Chunk 1: Critical — Worker thread safety

Three issues that cause tasks to be orphaned permanently when a worker dies mid-task.

### Task 1: Protect status-reset in embedding_worker.process_task

**File:** `workers/embedding_worker.py` (lines 96–113)

**Problem:** When Qdrant upsert fails, the handler calls `manager.update_task_status(...)` to reset the task to EXTRACTED. If SQLite is locked at that moment, this call throws a second exception. The `raise` on the next line is never reached, and the task is permanently stuck in EMBEDDING state.

- [ ] **Step 1: Read the current upsert exception block**

  ```python
  # workers/embedding_worker.py ~line 96
  except Exception as e:
      logger.error(f"Qdrant upsert failed on chunk {i}: {e}. Resetting task to EXTRACTED.")
      manager.update_task_status(db_path, file_hash, status='EXTRACTED')
      raise  # re-raise so outer loop detects connection loss and resets vs
  ```

- [ ] **Step 2: Wrap the status-reset in its own try/except**

  Replace the except block with:
  ```python
  except Exception as e:
      logger.error(f"Qdrant upsert failed on chunk {i}: {e}. Resetting task to EXTRACTED.")
      try:
          manager.update_task_status(db_path, file_hash, status='EXTRACTED')
      except Exception as e2:
          logger.error(
              f"Embedding worker: could not reset task {file_hash[:8]} to EXTRACTED — "
              f"task may be stuck in EMBEDDING: {e2}"
          )
      raise  # re-raise so outer loop detects connection loss and resets vs
  ```

- [ ] **Step 3: Verify the raise still executes**

  The `raise` is now unconditional — it executes whether or not the status-reset succeeded. Confirm this by reading the surrounding code and checking that `raise` is not inside either try block.

- [ ] **Step 4: Commit**

  ```bash
  git add workers/embedding_worker.py
  git commit -m "fix(worker): protect Qdrant task-reset from SQLite lock in embedding worker"
  ```

---

### Task 2: Reset orphaned tasks when watchdog restarts a dead thread

**File:** `run.py` (the `_watchdog` function, lines 82–99)

**Problem:** `manager.reset_stuck_tasks()` only runs at startup. If the watchdog revives a dead extraction or embedding worker, any task that worker held in PROCESSING or EMBEDDING is permanently orphaned until the next full restart.

- [ ] **Step 1: Read the current watchdog restart block**

  ```python
  # run.py ~line 85
  if not t.is_alive():
      logger.error(f"Watchdog: {name} thread died — restarting")
      try:
          from core.alerts import send_alert
          send_alert(...)
      except Exception:
          pass
      new_t = threading.Thread(target=target, args=args, daemon=True, name=name)
      new_t.start()
      live[name] = (new_t, target, args)
  ```

- [ ] **Step 2: Add reset_stuck_tasks() call before the restart**

  Replace the `if not t.is_alive():` block with:
  ```python
  if not t.is_alive():
      logger.error(f"Watchdog: {name} thread died — restarting")
      # Reset any tasks the dead thread was holding in PROCESSING or EMBEDDING
      try:
          n = manager.reset_stuck_tasks(DB_PATH)
          if n:
              logger.info(f"Watchdog: reset {n} orphaned task(s) after {name} death")
      except Exception as e:
          logger.error(f"Watchdog: could not reset stuck tasks: {e}")
      try:
          from core.alerts import send_alert
          send_alert(
              title=f"Worker restarted: {name}",
              message=f"DocVault watchdog detected that '{name}' died and restarted it automatically.",
              level='warning',
              source='watchdog',
          )
      except Exception:
          pass
      new_t = threading.Thread(target=target, args=args, daemon=True, name=name)
      new_t.start()
      live[name] = (new_t, target, args)
  ```

  `DB_PATH` is a module-level variable in `run.py` and is already accessible inside `_watchdog`.

- [ ] **Step 3: Commit**

  ```bash
  git add run.py
  git commit -m "fix(watchdog): reset orphaned PROCESSING/EMBEDDING tasks on thread restart"
  ```

---

### Task 3: Protect thread.start() inside the watchdog

**File:** `run.py` (`_watchdog` function)

**Problem:** `new_t.start()` is called without a try/except. If the OS exhausts its thread limit, `RuntimeError` propagates out of the loop and kills the watchdog entirely — all future auto-recovery ceases.

- [ ] **Step 1: Wrap the thread creation and start in try/except**

  Replace the thread creation/start block (added/modified in Task 2) with:
  ```python
  try:
      new_t = threading.Thread(target=target, args=args, daemon=True, name=name)
      new_t.start()
      live[name] = (new_t, target, args)
      logger.info(f"Watchdog: {name} restarted successfully")
  except Exception as start_err:
      logger.error(
          f"Watchdog: could not restart {name}: {start_err}. "
          f"Will retry in {interval}s."
      )
      # Leave the dead entry in live{} so we retry next interval
  ```

- [ ] **Step 2: Verify the outer watchdog loop has no top-level try/except that would swallow errors differently**

  The `while True: time.sleep(interval); for name, ...` structure means a failed start simply leaves the dead thread in `live`. Next cycle it tries again. Confirm there is no outer try/except hiding this.

- [ ] **Step 3: Commit**

  ```bash
  git add run.py
  git commit -m "fix(watchdog): protect thread.start() from RuntimeError to keep watchdog alive"
  ```

---

## Chunk 2: Critical + High — Monitor daemon and subprocess deadlock

### Task 4: Guard HardwareMonitor.run() against unhandled exceptions

**File:** `core/monitor.py` (the `HardwareMonitor.run` method, lines 558–569)

**Problem:** The `while True:` loop in `run()` has no top-level exception handler. If any sensor or state-machine call throws unexpectedly, the thread dies. Because `t_monitor` is not in the watchdog's managed list, it never restarts. If the system was in `cooldown` state at the time, all workers are frozen for the rest of the uptime.

- [ ] **Step 1: Read the current run() method**

  ```python
  def run(self):
      """Main daemon loop. Call from a daemon thread."""
      logger.info("Resource governor starting", ext="monitor")
      while True:
          if self._is_enabled():
              reading    = self._sample()
              thresholds = _load_thresholds()
              self._sm.update(reading, thresholds)
              _set_throttle_state(self._sm.state, reading, self._sm.reason)
              _record_sample(reading, self._sm.state)
              self._check_stall()
          time.sleep(self._get_interval())
  ```

- [ ] **Step 2: Wrap the loop body in try/except**

  ```python
  def run(self):
      """Main daemon loop. Call from a daemon thread."""
      logger.info("Resource governor starting", ext="monitor")
      while True:
          try:
              if self._is_enabled():
                  reading    = self._sample()
                  thresholds = _load_thresholds()
                  self._sm.update(reading, thresholds)
                  _set_throttle_state(self._sm.state, reading, self._sm.reason)
                  _record_sample(reading, self._sm.state)
                  self._check_stall()
          except Exception as e:
              logger.error(f"Resource governor cycle error (continuing): {e}", ext="monitor")
          time.sleep(self._get_interval())
  ```

  Note: `time.sleep(self._get_interval())` stays **outside** the try block so a cycle error does not spin-loop — the monitor always sleeps between cycles.

- [ ] **Step 3: Commit**

  ```bash
  git add core/monitor.py
  git commit -m "fix(monitor): guard HardwareMonitor.run() against unhandled exceptions"
  ```

---

### Task 5: Fix ollama_governor tuple unpacking bug

**File:** `core/monitor.py` (`ollama_governor` context manager, lines 388 and 397)

**Problem:** `get_throttle_state()` returns a `(state, reason)` tuple. The governor assigns the whole tuple to `state` and checks `if state in ('throttled', 'cooldown')`. A tuple is never equal to either string, so the thermal serialization lock is **never applied** — workers run at full concurrency even under heat stress.

- [ ] **Step 1: Read the two affected lines**

  ```python
  # Line ~388 (inside the semaphore branch)
  state = get_throttle_state()
  if state in ('throttled', 'cooldown'):

  # Line ~397 (inside the no-semaphore branch)
  state = get_throttle_state()
  if state in ('throttled', 'cooldown'):
  ```

- [ ] **Step 2: Fix both occurrences to unpack the tuple**

  ```python
  # Line ~388
  state, _reason = get_throttle_state()
  if state in ('throttled', 'cooldown'):

  # Line ~397
  state, _reason = get_throttle_state()
  if state in ('throttled', 'cooldown'):
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add core/monitor.py
  git commit -m "fix(monitor): unpack get_throttle_state() tuple in ollama_governor"
  ```

---

### Task 6: Fix subprocess pipe deadlock in SubprocessExtractorAdapter

**File:** `core/extractors/base.py` (`SubprocessExtractorAdapter.extract`, lines 376–430)

**Problem:** The adapter polls `process.poll()` in a sleep loop and only calls `process.communicate()` after the process exits. If a kernel writes >64KB to stdout or stderr (the typical OS pipe buffer), the subprocess blocks on `write()` waiting for Python to read. Python is sleeping. Deadlock until the 300s kill timeout fires. Any verbose kernel fails 100% of the time.

**Fix strategy:** Move to a background-thread approach: run `process.communicate()` in a daemon thread (which drains both pipes continuously), while the main thread polls for cancellation and timeout as before.

- [ ] **Step 1: Read the current extract() method in SubprocessExtractorAdapter (lines 367–430)**

  Current flow:
  ```python
  process = subprocess.Popen(cmd, stdout=PIPE, stderr=PIPE, text=True)
  while process.poll() is None:
      check cancel / timeout
      time.sleep(0.5)
  stdout, stderr = process.communicate()   # <-- deadlock here
  ```

- [ ] **Step 2: Replace the extract() method body with the thread-drain approach**

  Replace from `# 2. Execute` through the end of the `try:` block with:
  ```python
      # 2. Execute
      ctx.logger.info(f"Invoking binary: {' '.join(cmd)}")
      try:
          process = subprocess.Popen(
              cmd,
              stdout=subprocess.PIPE,
              stderr=subprocess.PIPE,
              text=True
          )

          # Drain stdout/stderr in a background thread to prevent pipe-buffer deadlock.
          # A kernel that writes >64KB to either pipe would otherwise block on write(),
          # while this thread is sleeping in poll() — classic OS deadlock.
          _result: dict = {}

          def _communicate():
              out, err = process.communicate()
              _result['stdout'] = out
              _result['stderr'] = err

          comm_thread = threading.Thread(target=_communicate, daemon=True)
          comm_thread.start()

          timeout = ctx.timeout_secs or 300
          start_time = time.time()

          while comm_thread.is_alive():
              if ctx.cancel_token.is_set():
                  process.kill()
                  comm_thread.join(timeout=5)
                  ctx.logger.warning(f"Killed subprocess {self.name} due to cancellation.")
                  return None, "Extraction cancelled by user", {}
              if time.time() - start_time > timeout:
                  process.kill()
                  comm_thread.join(timeout=5)
                  ctx.logger.error(f"Killed subprocess {self.name} due to timeout.")
                  return None, "Binary execution timed out", {}
              time.sleep(0.5)

          stdout = _result.get('stdout', '')
          stderr = _result.get('stderr', '')

          # 3. Capture Stderr (Logs/Progress)
          if stderr:
              ctx.logger.debug(f"Binary STDERR: {stderr.strip()}")

          if process.returncode != 0:
              return None, f"Binary exited with code {process.returncode}: {stderr}", {}

          # 4. Parse Stdout (JSON Contract)
          if not stdout.strip():
              return None, "Binary returned no stdout", {}

          try:
              data = json.loads(stdout)
          except json.JSONDecodeError:
              return None, f"Binary output violated JSON contract: {stdout[:100]}...", {}

          text = data.get("text")
          error = data.get("error")
          meta = data.get("metadata", {})

          return text, error, meta

      except Exception as e:
          return None, f"Subprocess failed: {e}", {}
  ```

  Note: `threading` is already imported at the top of `base.py`.

- [ ] **Step 3: Commit**

  ```bash
  git add core/extractors/base.py
  git commit -m "fix(extractor): drain subprocess pipes in thread to prevent deadlock on large output"
  ```

---

## Chunk 3: High + Medium — Ingestor, manager, embedder

### Task 7: Broaden exception catch in ingestor to include SQLite errors

**File:** `core/ingestor.py` (lines 119–145)

**Problem:** The per-file exception handler catches `(OSError, PermissionError)` but not `sqlite3.OperationalError`. A database lock during `manager.insert_task()` or `manager.get_task()` raises uncaught and terminates the entire vault directory scan.

There are two exception handlers to fix: one in the folder intelligence block (~line 95) and one in the file loop (~line 144).

- [ ] **Step 1: Find both exception handlers**

  ```python
  # Folder intelligence block (~line 95): no exception handler at all
  if not manager.get_task(db_path, folder_hash):
      manager.insert_task(...)   # unprotected

  # File loop (~line 144)
  except (OSError, PermissionError) as e:
      logger.warn(f"  Skipped {name}: {e}")
  ```

- [ ] **Step 2: Wrap the folder intelligence block**

  The `manager.get_task()` and `manager.insert_task()` calls in the folder block have no exception handler. Wrap the whole block:
  ```python
  for ext, count in ext_counts.items():
      if count >= len(files) * 0.5 and get_folder_extractors(ext):
          try:
              if not manager.get_task(db_path, folder_hash):
                  manager.insert_task(
                      db_path, folder_hash, root_norm, f"directory/{ext}", 5,
                      vault_id=vault_id
                  )
                  logger.info(f"  Added Directory Unit: {os.path.basename(root)} (type: {ext})")
                  added += 1
          except Exception as e:
              logger.warn(f"  Skipped directory {os.path.basename(root)}: {e}")
          break
  ```

- [ ] **Step 3: Broaden the file loop exception catch**

  Change:
  ```python
  except (OSError, PermissionError) as e:
      logger.warn(f"  Skipped {name}: {e}")
  ```
  To:
  ```python
  except Exception as e:
      logger.warn(f"  Skipped {name}: {e}")
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add core/ingestor.py
  git commit -m "fix(ingestor): catch all exceptions per-file to prevent mid-scan abort"
  ```

---

### Task 8: Protect FTS delete inside complete_extraction

**File:** `core/manager.py` (`complete_extraction`, lines 554–573)

**Problem:** The `DELETE FROM fts_index` runs before the `try/except` block that guards chunking. If the FTS table is locked, `DELETE` throws, the entire function crashes before `conn.commit()`, and the status update rolls back — leaving a completed task stuck in PROCESSING forever.

- [ ] **Step 1: Read the current FTS block**

  ```python
  # core/manager.py ~line 554
  if text:
      row = conn.execute(
          "SELECT file_path FROM tasks WHERE file_hash = ?", (file_hash,)
      ).fetchone()
      if row:
          conn.execute(
              "DELETE FROM fts_index WHERE file_hash = ?", (file_hash,)
          )                                                  # <-- unprotected
          try:
              from embeddings.chunker import chunk
              chunks = chunk(text)
              for i, chunk_text in enumerate(chunks):
                  conn.execute(
                      "INSERT INTO fts_index ...", (...)
                  )
          except Exception as e:
              print(f"[manager] Warning: FTS chunk indexing failed: {e}")
  conn.commit()
  ```

- [ ] **Step 2: Move DELETE inside the try/except**

  ```python
  if text:
      row = conn.execute(
          "SELECT file_path FROM tasks WHERE file_hash = ?", (file_hash,)
      ).fetchone()
      if row:
          try:
              conn.execute(
                  "DELETE FROM fts_index WHERE file_hash = ?", (file_hash,)
              )
              from embeddings.chunker import chunk
              chunks = chunk(text)
              for i, chunk_text in enumerate(chunks):
                  conn.execute(
                      "INSERT INTO fts_index (file_hash, chunk_index, file_path, content) "
                      "VALUES (?, ?, ?, ?)",
                      (file_hash, i, row['file_path'], chunk_text)
                  )
          except Exception as e:
              print(f"[manager] Warning: FTS index update failed: {e}")
  conn.commit()
  ```

  The status `UPDATE` at the top of the function is now always committed even if FTS fails. Extraction is preserved; FTS is best-effort.

- [ ] **Step 3: Commit**

  ```bash
  git add core/manager.py
  git commit -m "fix(manager): guard FTS delete inside complete_extraction to prevent task state rollback"
  ```

---

### Task 9: Add HTTP timeout to Ollama embedding calls

**File:** `embeddings/embedder.py` (line 18)

**Problem:** `ollama.embeddings()` has no explicit timeout. If Ollama keeps the socket open but never responds, the embedding worker blocks indefinitely, holding the `ollama_governor()` semaphore slot. Eventually all parallel slots are occupied by hanging calls.

**Fix:** Use `ollama.Client(timeout=N)` instead of the module-level `ollama.embeddings()`. The `ollama` library passes timeout to its underlying `httpx` client.

- [ ] **Step 1: Read the current embed() function**

  ```python
  def embed(text: str) -> list[float] | None:
      from core.monitor import ollama_governor
      model = settings.get('ollama:embed_model')

      for attempt in range(_NO_SLOTS_RETRIES + 1):
          try:
              with ollama_governor():
                  response = ollama.embeddings(model=model, prompt=text)
                  return response['embedding']
          except Exception as e:
              ...
  ```

- [ ] **Step 2: Replace with a timeout-bearing client call**

  ```python
  def embed(text: str) -> list[float] | None:
      """Generate an embedding vector for the given text using Ollama."""
      from core.monitor import ollama_governor
      model           = settings.get('ollama:embed_model')
      timeout_secs    = int(settings.get('ollama:embed_timeout') or 120)

      for attempt in range(_NO_SLOTS_RETRIES + 1):
          try:
              with ollama_governor():
                  client   = ollama.Client(timeout=timeout_secs)
                  response = client.embeddings(model=model, prompt=text)
                  return response['embedding']
          except Exception as e:
              err = str(e)
              if 'no slots' in err.lower() and attempt < _NO_SLOTS_RETRIES:
                  wait = _NO_SLOTS_BACKOFF[attempt]
                  print(f"  [embed] Ollama busy (no slots), retry {attempt + 1}/{_NO_SLOTS_RETRIES} in {wait}s…")
                  time.sleep(wait)
                  continue
              print(f"  [embed] Error: {e}")
              return None
  ```

  A new `Client` instance per call is lightweight (stateless HTTP) and ensures the timeout is read from settings at call time (not import time), consistent with the rest of the codebase.

- [ ] **Step 3: Add `ollama:embed_timeout` to the settings schema**

  In `core/settings.py`, find the `ollama:` section in the schema dict. Entries are plain dicts — there is no `SchemaEntry` class. Add:
  ```python
  'ollama:embed_timeout': {
      'type': 'int', 'default': 120, 'label': 'Embedding Timeout (s)', 'group': 'ollama',
      'description': 'HTTP timeout in seconds for Ollama embedding calls. '
                     'Prevents indefinite hangs when Ollama is unresponsive.',
  },
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add embeddings/embedder.py core/settings.py
  git commit -m "fix(embedder): add HTTP timeout to Ollama embedding calls via Client(timeout=N)"
  ```

---

## Chunk 4: High + Low — Art worker crash loop and log clarity

### Task 10: Guard _write_nfo() calls in art_enrichment_worker exception handlers

**File:** `workers/art_enrichment_worker.py` (the `for image_path in queue:` loop)

**Problem:** `_write_nfo()` is called inside several `except` blocks with no protection. A `PermissionError` or full-disk error inside `_write_nfo()` propagates out of the except block, exits the `for` loop, and kills the thread. The watchdog then restarts it. It picks up the same image, hits the same error, crashes again. Tight infinite crash loop.

**Fix:** Wrap the entire `for image_path in queue:` body in a top-level `try/except` so any unhandled exception skips the current image and continues to the next.

- [ ] **Step 1: Read the for loop structure in art_enrichment_worker.run()**

  The loop runs from approximately line 717 (`for image_path in queue:`) through line 850. It has inner try/except blocks for API calls but no outer guard.

- [ ] **Step 2: Add a top-level try/except around the loop body**

  The loop body starts immediately after `for image_path in queue:`. Indent the entire body one level and add:

  ```python
  for image_path in queue:
      try:
          if shutdown_event and shutdown_event.is_set():
              break

          # Re-check throttle before each image
          # ... (all existing loop body code, indented one extra level) ...

      except Exception as _loop_err:
          logger.error(
              f"Art enrichment: unhandled error for "
              f"{os.path.basename(image_path)} — skipping: {_loop_err}",
              ext="art"
          )
          continue
  ```

  This catches any exception that escapes the inner handlers (including a `_write_nfo()` failure) and continues to the next image instead of crashing.

- [ ] **Step 3: Commit**

  ```bash
  git add workers/art_enrichment_worker.py
  git commit -m "fix(art-worker): guard per-image loop body to prevent crash loop on _write_nfo failure"
  ```

---

### Task 11: Fix misleading log message in extraction_worker

**File:** `workers/extraction_worker.py` (line 197)

**Problem:** When the nested exception handler cannot mark a task ERROR, it logs `"will retry next run"`. This is false — the task is in PROCESSING, which the claim query never touches. The task is permanently lost, not retried. This makes debugging future incidents much harder.

- [ ] **Step 1: Find the line**

  ```python
  # workers/extraction_worker.py ~line 197
  logger.error(f"Extraction worker: could not mark task ERROR (will retry next run): {e2}")
  ```

- [ ] **Step 2: Replace with an accurate message**

  ```python
  logger.error(
      f"Extraction worker: could not mark task {task['file_hash'][:8]} ERROR — "
      f"task is stuck in PROCESSING and will not be retried until server restart: {e2}"
  )
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add workers/extraction_worker.py
  git commit -m "fix(worker): correct misleading 'will retry' log message for stuck PROCESSING tasks"
  ```

---

### Task 12: Add stderr fallback to ExtractorLogger when logs.db is unavailable

**File:** `core/extractors/base.py` (`ExtractorLogger._write_log` and `_write_error`, lines 103–131)

**Problem:** Both methods swallow all exceptions with bare `except Exception: pass`. If `logs.db` becomes corrupted or unavailable, all diagnostic visibility silently vanishes. Future pipeline freezes become impossible to debug via logs.

- [ ] **Step 1: Read the current _write_log and _write_error methods**

  ```python
  def _write_log(self, level: str, message: str):
      try:
          ...DB write...
      except Exception:
          pass  # never let logging kill an extractor

  def _write_error(self, level: str, message: str, error_type: str = '', tb: str = ''):
      try:
          ...DB write...
      except Exception:
          pass
  ```

- [ ] **Step 2: Add stderr fallback to both methods**

  ```python
  def _write_log(self, level: str, message: str):
      try:
          from core.manager import get_logs_db_path, _connect
          with _connect(get_logs_db_path()) as conn:
              conn.execute(
                  """INSERT INTO worker_log
                     (file_hash, vault_id, extractor, level, message, occurred_at)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (self.file_hash, self.vault_id, self.extractor_name,
                   level, message, self._now())
              )
              conn.commit()
      except Exception as log_err:
          import sys
          print(
              f"[ExtractorLogger._write_log] DB unavailable ({log_err}): "
              f"[{self.extractor_name}] {level}: {message}",
              file=sys.stderr
          )

  def _write_error(self, level: str, message: str, error_type: str = '', tb: str = ''):
      try:
          from core.manager import get_logs_db_path, _connect
          with _connect(get_logs_db_path()) as conn:
              conn.execute(
                  """INSERT INTO worker_errors
                     (file_hash, vault_id, extractor, error_type, error_message, traceback, occurred_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""",
                  (self.file_hash, self.vault_id, self.extractor_name,
                   error_type or level, message, tb, self._now())
              )
              conn.commit()
      except Exception as log_err:
          import sys
          print(
              f"[ExtractorLogger._write_error] DB unavailable ({log_err}): "
              f"[{self.extractor_name}] {level}: {message}",
              file=sys.stderr
          )
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add core/extractors/base.py
  git commit -m "fix(extractor): add stderr fallback to ExtractorLogger when logs.db is unavailable"
  ```

---

## Final verification

- [ ] Restart the server and confirm all workers start cleanly
- [ ] Check the console for any import errors or startup exceptions
- [ ] Verify `/api/workers/status` returns normally
- [ ] Confirm `ollama_governor()` now properly serializes under thermal pressure by checking that `state, _reason = get_throttle_state()` is used
