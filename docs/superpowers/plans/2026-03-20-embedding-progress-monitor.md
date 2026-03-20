# Embedding Progress Monitor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show real-time embedding progress (file, chunk X/Y, %, elapsed, ETA) in the navbar sensor rail and the vault page so large-file embedding no longer appears as a frozen pipeline.

**Architecture:** Extend `/api/workers/status` with an `embedding_progress` object built from the live EMBEDDING task row + an in-module start-time dict. The sensor rail (`lcars.js`) reads the new field on its existing 5 s poll; `vault.html` adds its own 5 s `setInterval` to fetch and render a progress card.

**Tech Stack:** Python/FastAPI (workers.py), vanilla JS (lcars.js, vault.html), SQLite (docvault.db tasks table)

**Spec:** `docs/superpowers/specs/2026-03-20-embedding-progress-monitor-design.md`

---

## Chunk 1: API — extend `/api/workers/status`

### Task 1: Add embedding progress to workers.py

**Files:**
- Modify: `api/routes/workers.py`

- [ ] **Step 1: Confirm DB columns exist**

  Run to verify `progress_text` and `progress_pct` are present in `tasks`:
  ```bash
  python -c "import sqlite3; c=sqlite3.connect('docvault.db'); print([r[1] for r in c.execute('PRAGMA table_info(tasks)')])"
  ```
  Expected output includes: `'progress_text'`, `'progress_pct'`

  (These columns are written by `manager.update_task_progress()` which the
  embedding worker calls for every chunk. They exist in the current schema.)

- [ ] **Step 2: Read the current file**

  Open `api/routes/workers.py`. Current shape:
  ```python
  @router.get("/workers/status")
  def worker_status():
      from core.monitor import get_stall_state
      stalled, stall_mins = get_stall_state()
      return {
          'paused': manager.get_pause_state(),
          'stalled': stalled,
          'stall_minutes': stall_mins,
      }
  ```

- [ ] **Step 3: Add the module-level start-time dict and helper**

  Add after the `router = APIRouter()` line:

  ```python
  import os
  import time

  # Tracks wall-clock start time for in-flight EMBEDDING tasks.
  # Key: file_hash  Value: time.time() when first seen in EMBEDDING state
  _embed_start: dict[str, float] = {}


  def _get_embedding_progress(db_path: str) -> dict | None:
      """
      Query the current EMBEDDING task and return progress info, or None.
      Maintains _embed_start for elapsed/ETA calculation.
      """
      import sqlite3
      try:
          con = sqlite3.connect(db_path)
          con.row_factory = sqlite3.Row
          try:
              row = con.execute(
                  "SELECT file_hash, file_path, progress_text, progress_pct "
                  "FROM tasks WHERE status='EMBEDDING' LIMIT 1"
              ).fetchone()
          finally:
              con.close()
      except Exception:
          return None

      # Evict hashes no longer in EMBEDDING state
      current_hash = row['file_hash'] if row else None
      for h in list(_embed_start.keys()):
          if h != current_hash:
              del _embed_start[h]

      if not row:
          return None

      fhash = row['file_hash']
      if fhash not in _embed_start:
          _embed_start[fhash] = time.time()

      pct = int(row['progress_pct'] or 0)
      elapsed = time.time() - _embed_start[fhash]
      eta = (elapsed / pct * (100 - pct)) if pct >= 2 else None

      return {
          'filename':      os.path.basename(row['file_path']),
          'progress_text': row['progress_text'] or '',
          'pct':           pct,
          'elapsed_secs':  round(elapsed),
          'eta_secs':      round(eta) if eta is not None else None,
      }
  ```

- [ ] **Step 4: Update `worker_status()` to call the helper**

  `get_db()` is already defined in `workers.py` (lines 7–9) — it returns
  `DB_PATH` from `api.main`. No import needed.

  Replace the existing `worker_status` function:

  ```python
  @router.get("/workers/status")
  def worker_status():
      from core.monitor import get_stall_state
      stalled, stall_mins = get_stall_state()
      return {
          'paused':             manager.get_pause_state(),
          'stalled':            stalled,
          'stall_minutes':      stall_mins,
          'embedding_progress': _get_embedding_progress(get_db()),
      }
  ```

- [ ] **Step 5: Verify manually**

  With the server running and an EMBEDDING task active, hit the endpoint:
  ```
  curl http://localhost:8000/api/workers/status
  ```
  Expected when embedding is active:
  ```json
  {
    "paused": false,
    "stalled": false,
    "stall_minutes": 0,
    "embedding_progress": {
      "filename": "DOC0012.XML",
      "progress_text": "Embedding chunk 67/1747...",
      "pct": 3,
      "elapsed_secs": 74,
      "eta_secs": 2386
    }
  }
  ```
  Expected when idle: `"embedding_progress": null`

- [ ] **Step 6: Commit**

  ```bash
  git add api/routes/workers.py
  git commit -m "feat(api): add embedding_progress to /api/workers/status"
  ```

---

## Chunk 2: Sensor Rail — lcars.js Workers sensor

### Task 2: Show chunk progress in the navbar Workers sensor

**Files:**
- Modify: `frontend/static/lcars.js` (lines 75–86, the `if (wrk)` block)

- [ ] **Step 1: Locate the Workers sensor update block**

  In `lcars.js`, find the `if (wrk)` block inside `lcPollSensors()`:
  ```javascript
  if (wrk) {
    const paused  = wrk.paused   ?? false;
    const stalled = wrk.stalled  ?? false;
    const stallM  = wrk.stall_minutes ?? 0;
    if (stalled) {
      _setSensor('lc-s-workers', `⚠ STALLED ${stallM}m`, 'err');
    } else if (paused) {
      _setSensor('lc-s-workers', '⏸ PAUSED', 'warn');
    } else {
      _setSensor('lc-s-workers', '▶ RUNNING', 'ok');
    }
  }
  ```

- [ ] **Step 2: Replace the `else` branch to show embedding progress**

  Replace the entire `if (wrk)` block with:
  ```javascript
  if (wrk) {
    const paused  = wrk.paused   ?? false;
    const stalled = wrk.stalled  ?? false;
    const stallM  = wrk.stall_minutes ?? 0;
    const ep      = wrk.embedding_progress ?? null;
    if (stalled) {
      _setSensor('lc-s-workers', `⚠ STALLED ${stallM}m`, 'err');
    } else if (paused) {
      _setSensor('lc-s-workers', '⏸ PAUSED', 'warn');
    } else if (ep) {
      const m = (ep.progress_text || '').match(/(\d+)\/(\d+)/);
      const label = m ? `▶ ${ep.pct}% · ${m[1]}/${m[2]}` : '▶ RUNNING';
      _setSensor('lc-s-workers', label, 'ok');
    } else {
      _setSensor('lc-s-workers', '▶ RUNNING', 'ok');
    }
  }
  ```

- [ ] **Step 3: Verify manually**

  Open any DocVault page with the navbar. While an EMBEDDING task is active the
  Workers sensor should display e.g. `▶ 3% · 67/1747`. When idle it shows
  `▶ RUNNING`.

- [ ] **Step 4: Commit**

  ```bash
  git add frontend/static/lcars.js
  git commit -m "feat(ui): show embedding chunk progress in navbar sensor rail"
  ```

---

## Chunk 3: Vault Page — embedding progress card

### Task 3: Add embedding progress card to vault.html

**Files:**
- Modify: `frontend/vault.html`

- [ ] **Step 1: Add the progress card HTML**

  In `vault.html`, locate the stat grid section:
  ```html
  <!-- Stat grid (6 tiles, color-coded) -->
  <div class="lc-stat-grid" id="stat-grid">
    <!-- populated by loadStats() -->
  </div>
  ```

  Insert the progress card **above** the stat grid:
  ```html
  <!-- Embedding progress card (hidden when idle) -->
  <div id="embed-progress-card" style="display:none;margin-bottom:12px;">
    <div class="lc-bar-badge" style="cursor:default;justify-content:space-between;align-items:center;padding:8px 14px;gap:12px;">
      <span style="color:var(--c-accent);font-size:11px;font-weight:700;letter-spacing:.08em;white-space:nowrap;">EMBEDDING</span>
      <span id="ep-filename" style="color:var(--c-accent);font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 12px;"></span>
      <span style="font-size:11px;color:var(--c-label-dim);">ETA&nbsp;<span id="ep-eta" style="color:var(--c-value);">—</span></span>
      <span id="ep-pct" style="color:var(--c-value);font-size:13px;font-weight:700;min-width:36px;text-align:right;"></span>
    </div>
    <div style="padding:6px 14px 10px;">
      <div style="background:var(--c-bar-bg,#1a1a1a);border-radius:2px;height:6px;overflow:hidden;margin-bottom:6px;">
        <div id="ep-bar" style="height:100%;background:var(--c-accent);width:0%;transition:width .4s ease;"></div>
      </div>
      <div style="display:flex;justify-content:space-between;">
        <span id="ep-chunks" style="font-size:11px;color:var(--c-label-dim);"></span>
        <span style="font-size:11px;color:var(--c-label-dim);">elapsed&nbsp;<span id="ep-elapsed" style="color:var(--c-value);">—</span></span>
      </div>
    </div>
  </div>

  <!-- Stat grid (6 tiles, color-coded) -->
  <div class="lc-stat-grid" id="stat-grid">
    <!-- populated by loadStats() -->
  </div>
  ```

- [ ] **Step 2: Add `fmtSecs` helper and `updateEmbedProgress` function**

  In `vault.html`, locate the `<script>` block. Add these two functions near the
  top of the script section (before `loadStats`):

  ```javascript
  function fmtSecs(s) {
    if (s == null) return '—';
    if (s < 60)   return `${Math.round(s)}s`;
    if (s < 3600) return `${Math.floor(s/60)}m ${Math.round(s%60)}s`;
    return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
  }

  async function updateEmbedProgress() {
    try {
      const wrk = await api('/workers/status');
      const ep  = wrk && wrk.embedding_progress;
      const card = document.getElementById('embed-progress-card');
      if (!card) return;
      if (!ep) {
        card.style.display = 'none';
        return;
      }
      card.style.display = '';
      document.getElementById('ep-filename').textContent = ep.filename || '';
      document.getElementById('ep-pct').textContent      = `${ep.pct}%`;
      document.getElementById('ep-bar').style.width      = `${ep.pct}%`;
      document.getElementById('ep-eta').textContent      = fmtSecs(ep.eta_secs);
      document.getElementById('ep-elapsed').textContent  = fmtSecs(ep.elapsed_secs);
      // Parse chunk numbers from progress_text e.g. "Embedding chunk 67/1747..."
      const m = (ep.progress_text || '').match(/(\d+)\/(\d+)/);
      document.getElementById('ep-chunks').textContent   = m ? `chunk ${m[1]} / ${m[2]}` : '';
    } catch (_) {}
  }
  ```

- [ ] **Step 3: Wire up the polling interval**

  Locate the `setInterval` block near the bottom of the script (around line 908):
  ```javascript
  setInterval(loadStats, 5000);
  setInterval(loadVaultCards, 30000);
  ```

  Add the new interval and an immediate first call:
  ```javascript
  updateEmbedProgress();
  setInterval(updateEmbedProgress, 5000);
  setInterval(loadStats, 5000);
  setInterval(loadVaultCards, 30000);
  ```

- [ ] **Step 4: Verify manually**

  Open `http://localhost:8000/vault` while an EMBEDDING task is active. The
  progress card should appear above the stat tiles showing:
  - Filename in amber
  - Progress bar filling left to right
  - Chunk X / Y on the left, elapsed on the right
  - ETA top-right (shows `—` for first ~2%)

  When no embedding is active, the card should be completely hidden.

- [ ] **Step 5: Commit**

  ```bash
  git add frontend/vault.html
  git commit -m "feat(ui): add embedding progress card to vault page"
  ```

---

## Final verification

- [ ] Restart the server and confirm all three changes are live
- [ ] With a large EMBEDDING task in progress, verify:
  - Sensor rail shows `▶ 3% · 67/1747`
  - Vault page shows the progress card with bar, chunks, elapsed, ETA
  - When embedding finishes, both revert to idle state (`▶ RUNNING` / card hidden)
