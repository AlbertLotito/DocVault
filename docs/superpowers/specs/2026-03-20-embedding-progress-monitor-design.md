# Embedding Progress Monitor — Design Spec
_Date: 2026-03-20_

## Problem

When the embedding worker processes a large file (e.g. a 1,747-chunk XML), the
`COMPLETED` count freezes for 20–30 minutes. There is no visible indication that
work is in progress, causing false "pipeline stuck" alarms.

## Goal

Show real-time embedding progress — current file, chunk X/Y, percent, elapsed
time, and ETA — in both the navbar sensor rail and the vault page.

---

## Approach

Extend the existing `/api/workers/status` endpoint with an `embedding_progress`
object. Both the sensor rail (already polling this endpoint every 5 s) and the
vault page (new `setInterval`) read the new field. No new endpoints.

---

## Section 1 — API (`api/routes/workers.py`)

### In-memory start-time tracking

A module-level dict `_embed_start: dict[str, float]` maps `file_hash →
wall_clock_start`. Lifecycle (evaluated in this order each request):

1. Query the DB for the current EMBEDDING task (0 or 1 rows).
2. **If a task exists and its hash is not in `_embed_start`**: add it with
   `time.time()` as start.
3. **Evict stale entries**: remove any hash from `_embed_start` that is no
   longer present in the query result. Never do a full `{}` reset — that would
   lose the in-flight entry when evaluated in the same cycle a new task appears.

This gives elapsed time without a DB schema change. If the embedding worker
crashes and leaves a row permanently in EMBEDDING state, `elapsed_secs` will
keep growing (no upper cap). This edge case is accepted as out of scope; the
stall detector already alerts for that condition.

### DB query

```sql
SELECT file_hash, file_path, progress_text, progress_pct
FROM tasks
WHERE status = 'EMBEDDING'
LIMIT 1
```

### New response field

`GET /api/workers/status` gains `embedding_progress` (object or `null`).
`pct` is an integer 0–100 (matches the value written by the embedding worker).

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

When no EMBEDDING task exists, `"embedding_progress": null`.

### ETA calculation

```python
elapsed = now - _embed_start[file_hash]          # seconds
eta     = elapsed / pct * (100 - pct)            # linear extrapolation
```

`eta_secs` is `null` when `pct < 2` to suppress wild early estimates.
`pct` is integer 0–100 throughout — never normalised to 0.0–1.0.

---

## Section 2 — Sensor Rail (`frontend/static/lcars.js`)

`lcPollSensors()` already reads `/api/workers/status`. The Workers sensor
update logic uses priority ordering — `embedding_progress` display is
**subordinate to stalled and paused**; it is shown only when neither flag is
set:

| Priority | Condition | Display | Class |
|----------|-----------|---------|-------|
| 1 | `stalled` | `⚠ STALLED Xm` | `err` |
| 2 | `paused` | `⏸ PAUSED` | `warn` |
| 3 | `embedding_progress` present | `▶ 3% · 67/1747` | `ok` |
| 4 | otherwise | `▶ RUNNING` | `ok` |

The `3%` and `67/1747` values in priority-3 come from:
- `pct` field directly for the percentage.
- Regex `(\d+)\/(\d+)` applied to `progress_text` for chunk numbers.
- Falls back to `▶ RUNNING` if the regex does not match.

The display fits inside the existing `else` block (the branch that currently
shows `▶ RUNNING`), split into an inner `if (ep) … else` check.

---

## Section 3 — Vault Page (`frontend/vault.html`)

### Polling

`vault.html` adds its own `setInterval(updateEmbedProgress, 5000)` that fetches
`/api/workers/status` and updates the progress card. `lcars.js`'s
`lcPollSensors()` only updates the navbar sensor rail and provides no page-level
callback mechanism, so a separate fetch is required.

### Embedding progress card

A new LCARS bar badge rendered **only when `embedding_progress` is non-null**
(`display:none` otherwise). Positioned above the existing stat tiles.

Layout:

```
[EMBEDDING]  DOC0012.XML                               3%  ETA ~40m
             ████░░░░░░░░░░░░░░░░░  chunk 67 / 1747   elapsed 1m 14s
```

- **Label**: `EMBEDDING` badge (accent color, same style as other bar badges)
- **Filename**: `os.path.basename` applied server-side; displayed in accent color
- **Percent + ETA**: right-aligned; muted label / bright value
- **Progress bar**: full-width, LCARS bar fill style; width set to `pct + '%'`
- **Chunk count + elapsed**: second line, smaller muted text

### Helper display functions (added to vault.html `<script>`)

```js
function fmtSecs(s) {
  if (s == null) return '—';
  if (s < 60)   return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${Math.round(s%60)}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}
```

ETA label uses `fmtSecs(eta_secs)`. When `eta_secs` is `null` (pct < 2),
the ETA cell shows `—`.

---

## Out of Scope

- Extraction progress (separate worker — can be added later)
- Multiple simultaneous embedding workers (single worker assumed)
- Persisting progress to `logs.db`
- WebSocket / SSE real-time push (5 s polling is sufficient)
- Capping `elapsed_secs` when the worker crashes mid-task (stall detector
  already covers that alert path)
