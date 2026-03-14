# DocVault Troubleshooting Guide

DocVault is a local-first document intelligence platform. FastAPI backend on port 8000, Qdrant vector DB in Docker on port 6333, Ollama LLM on port 11434, SQLite databases (`docvault.db`, `settings.db`, `logs.db`).

---

## 1. Common Startup Failures

### Port 8000 already in use

```
Error: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)
```

Find the process using port 8000 and stop it.

- Windows: `netstat -ano | findstr :8000`, then `taskkill /PID <pid> /F`
- Linux/macOS: `lsof -i :8000`, then `kill <pid>`

Or change DocVault's bind port in the Settings UI (`server:port` key).

---

### Qdrant not connecting

```
ConnectionRefusedError: [Errno 111] Connection refused
```

1. `docker ps` — is `docvault-qdrant-1` listed as running?
2. If not running: `docker start docvault-qdrant-1`
3. If the container does not exist: `docker compose up -d qdrant`
4. Wait 10 seconds, then verify: `curl http://localhost:6333/collections`

---

### Qdrant degraded mode banner in Search UI

An amber banner appears in Search when Qdrant is unreachable. DocVault continues working — semantic search returns empty results, and hybrid search falls back to FTS-only. The banner disappears automatically when Qdrant reconnects. Fix Qdrant first (see above), then retry your search.

---

### Ollama not running

```
[!!] Ollama is not running. Embeddings and LLM features will fail.
```

Run `ollama serve` in a separate terminal. DocVault starts successfully without Ollama — extraction and FTS search continue to work. Embeddings and RAG fail until Ollama is reachable.

---

### Model not pulled

```
[!!] Model not pulled: deepseek-r1:14b  -- run: ollama pull deepseek-r1:14b
```

Run `ollama pull <model-name>`. Required models:

- `nomic-embed-text`
- `minicpm-v`
- `deepseek-r1:14b`

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

This deletes `docvault.db` and Qdrant vectors. `settings.db` is never touched — your config, API keys, and vault settings are preserved. Re-ingestion starts automatically on next `start.ps1`.

---

### Qdrant storage corruption (Qdrant refuses to start)

```bash
docker stop docvault-qdrant-1
```

Windows:
```powershell
Remove-Item qdrant_storage\collections\docvault -Recurse -Force
```

Linux/macOS:
```bash
rm -rf qdrant_storage/collections/docvault
```

Then:
```bash
docker start docvault-qdrant-1
```

Use the **Utilities page → Rebuild Index** to re-embed all extracted content. The `art_index` collection (in `qdrant_storage/collections/art_index`) is separate and unaffected.

---

## 4. Files Not Appearing in Search

Work through this checklist:

**1. Check the Vault Log** (Catalog page, filter by vault) — what status are the tasks showing?

| Status | Meaning |
|---|---|
| `PENDING` | Worker is running but file is queued. Normal for large collections. |
| `PROCESSING` for >30 minutes | Stuck. Use Utilities → Reset stuck tasks. |
| `EXTRACTED` but never `EMBEDDED` | Qdrant or Ollama may be down. |
| `ERROR` | See the error message in the Vault Log. |

**2. Check worker status** on the Index page — are all three workers running?

**3. Check Qdrant** — is the degraded banner showing in Search? If so, fix Qdrant first (see section 1).

**4. Re-trigger embedding** — if tasks are stuck at `EXTRACTED`, use **Utilities → Retry embed errors** to push them back through.
