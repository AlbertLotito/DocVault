# DocVault Troubleshooting Guide

DocVault is a local-first document intelligence platform. FastAPI backend on port 8050, embedded LanceDB vector store (no external service — data lives in `lancedb_storage/`), Ollama LLM on port 11434 (configurable — see `ollama:host`), SQLite databases (`docvault.db`, `settings.db`, `logs.db`).

---

## 1. Common Startup Failures

### Port 8050 already in use

```
Error: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8050)
```

Find the process using port 8050 and stop it.

- Windows: `netstat -ano | findstr :8050`, then `taskkill /PID <pid> /F`
- Linux/macOS: `lsof -i :8050`, then `kill <pid>`

Or change DocVault's bind port in `config.ini` (`[server] port`) or the Settings UI (`server:port` key).

---

### "Semantic search unavailable" banner in Search UI

An amber banner appears in Search when the semantic search step times out or errors — hybrid search falls back to FTS-only and the response includes `degraded: true`. Since LanceDB is an embedded library (not a separate service), this almost always means one of:

1. **Ollama is unreachable or slow to respond** — embedding the query requires a live Ollama connection. Check `ollama serve` is running and reachable at `ollama:host`.
2. **The semantic step exceeded `search:semantic_timeout`** (default 20s) — often caused by Ollama being busy with worker embedding batches. The query-embed path (`embedder.query_embed()`) bypasses the worker's embed semaphore specifically to avoid this; if it's still slow, check Ollama's load on the Telemetry dashboard.
3. **A previous bug** (fixed): scoping a search to a large vault (100K+ documents) caused `VectorStore.search()` to build a multi-megabyte SQL filter string, which LanceDB took 30s+ to evaluate. This is fixed — large hash filters now use an over-fetch + Python `set` post-filter instead of an inline SQL `IN (...)` clause (`embeddings/vector_store.py`).

The banner disappears automatically once a search completes within the timeout. No action is needed beyond addressing the cause above — DocVault continues to work in degraded mode (FTS-only) the whole time.

---

### Ollama not running

```
[!!] Ollama is not running. Embeddings and LLM features will fail.
```

Run `ollama serve` in a separate terminal. DocVault starts successfully without Ollama — extraction and FTS search continue to work. Embeddings and RAG fail until Ollama is reachable.

---

### Model not pulled

```
[!!] Model not pulled: qwen2.5:14b  -- run: ollama pull qwen2.5:14b
```

Run `ollama pull <model-name>`. Required models (defaults — configurable in Settings):

- `nomic-embed-text` — embeddings
- `minicpm-v` — vision/OCR
- `qwen2.5:14b` — RAG chat

---

### venv not found

```
[XX] venv not found. Run setup.ps1 first.
```

Run `.\setup.ps1`.

---

## 2. Extraction Errors

### charmap / codec encoding error (tasks stuck in ERROR)

```
[extractor] OCR failed: 'charmap' codec can't encode character '\u2019'
```

Cause: Unicode characters from OCR output encountered a Windows-1252 encode step. This is a known bug affecting OCR-heavy files on Windows.

Workaround: After a fix is applied to the extractor, use the **Utilities page → System Health → Retry extraction errors** button to reset affected tasks to PENDING and re-run them.

---

### WMI access denied (Windows, non-admin)

```
wmi.x_wmi: <x_wmi: Unexpected COM Error (-2147352567, ...)>
```

Cause: The WMI sensor needs administrator access on some Windows configurations.

Fix: Run DocVault as administrator. Or accept the degradation — CPU temperature will show 0°C but all other features work normally. Hardware throttling still functions using other sensors (GPU, disk).

---

### No Ollama slots / model already loaded

```
Error: model is already loaded
```

Cause: Ollama is busy with another request. DocVault serialises Ollama calls by default (`ollama:max_parallel = 1`). This resolves on its own as the queue clears.

If this happens frequently: check the Telemetry dashboard for GPU utilisation. Consider setting `ollama:max_parallel = 2` if your GPU has headroom.

---

### Whisper first-run takes 30–120 seconds

Expected behaviour. Whisper downloads its model on first use (~140 MB for `base`, up to 2.9 GB for `large-v3`). The spinner in the Extractor Lab shows elapsed time. Use `--timeout 600` if testing via `tools/test_kernel.py`.

---

## 3. Database Issues

### Database locked / OperationalError

```
sqlite3.OperationalError: database is locked
```

Cause: Multiple DocVault instances running simultaneously, or a previous crash left a write lock.

Fix:

1. Ensure only one DocVault process is running: `tasklist | findstr python` (Windows)
2. If no other process is running, the lock should clear on its own — wait 30 seconds and retry
3. Use **Utilities → System Health → Reset stuck tasks** to clear orphaned PROCESSING states

---

### Database corruption (DocVault fails to start)

Run `reset.ps1`:

```powershell
.\reset.ps1
```

This deletes `docvault.db` and the LanceDB vector tables. `settings.db` is never touched — your config, API keys, and vault settings are preserved. Re-ingestion starts automatically on next `start.ps1`.

---

### LanceDB storage corruption / search behaving oddly after a crash

LanceDB is an embedded library — its data lives entirely in `lancedb_storage/` on disk, with no separate process to restart. If a hard crash leaves it in a bad state:

Windows:
```powershell
Remove-Item lancedb_storage -Recurse -Force
```

Linux/macOS:
```bash
rm -rf lancedb_storage
```

Then use the **Utilities page → Rebuild Index** (`POST /api/utils/reindex`) to recreate the table and re-embed all extracted content — this also resets `COMPLETED` tasks back to `EXTRACTED` so the embedding worker picks them up again. The `art_index` table is separate and unaffected.

---

## 4. Files Not Appearing in Search

Work through this checklist:

**1. Check the Vault Log** (Catalog page, filter by vault) — what status are the tasks showing?

| Status | Meaning |
|---|---|
| `PENDING` | Worker is running but file is queued. Normal for large collections. |
| `PROCESSING` for >30 minutes | Stuck. Use Utilities → Reset stuck tasks. |
| `EXTRACTED` but never `EMBEDDED` | Ollama may be down/unreachable, or the embedding worker is paused (Search Mode pauses workers). |
| `ERROR` | See the error message in the Vault Log. |

**2. Check worker status** on the Vault Status page — are all three workers running, and not paused?

**3. Check semantic search health** — is the "Semantic search unavailable" banner showing in Search? If so, see section 1 above (almost always an Ollama reachability/timeout issue, not a LanceDB issue — LanceDB is embedded and has no separate process to fail).

**4. Re-trigger embedding** — if tasks are stuck at `EXTRACTED`, use **Utilities → Retry embed errors** to push them back through.
