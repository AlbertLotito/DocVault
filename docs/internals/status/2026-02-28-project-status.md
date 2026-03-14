# Project Status & Design Notes — 2026-02-28

This document summarises the current state of the DocVault project, including all architectural decisions and bug fixes across both development sessions.

---

## 1. Project Summary

DocVault is a local-first document intelligence platform. It scans a designated folder, extracts text and metadata from a wide variety of files (PDFs, Office documents, images, media), and makes all content searchable via a web interface. It uses a combination of full-text search (SQLite FTS5) and semantic vector search (Qdrant + Ollama) to provide a powerful query experience, including Retrieval-Augmented Generation (RAG) to answer questions based on document content.

---

## 2. Current Status: **Operational**

All core components are in place and the system is fully operational. The application can:

- Ingest files from a target directory on a continuous basis.
- Route different file types to the correct content extractors.
- Run extraction and embedding tasks in parallel background workers.
- Serve a web UI for viewing statistics, browsing the document catalog, searching, and querying via RAG.
- Persist all configuration changes at runtime via a Settings page — no restart required for most changes.

---

## 3. Feature & Fix History

### Session 1 (2026-02-26)

- **Scalable Catalog UI:** Sortable columns and full pagination for large document sets.
- **Detailed Dashboard:** Shows all in-progress pipeline states (`Processing`, `Extracted`, `Embedding`).
- **Continuous Ingestion Worker:** Periodically scans the source directory for new files.
- **Worker Task Prioritization:** Tasks are prioritised by file type (e.g., text before video).
- **Robust Shutdown:** Daemon threads ensure instant `Ctrl+C` exit.
- **Responsive Pause:** Pause flag interrupts idle workers within seconds.
- **Resilient Workers:** Enhanced error handling for Qdrant connection failures with auto-retry.
- **Configurable Tesseract:** Tesseract executable path is configurable.
- **Robust Text Chunking:** Character-based chunking prevents errors on oversized text.
- **Utilities Page:** Async Qdrant health check (write/read/delete round-trip).

### Session 2 (2026-02-28)

#### Settings System
- **`core/settings.py`** — New `Settings` class with a declared schema of all UI-configurable keys. Implements a 3-tier value lookup: DB override → `config.ini` → schema default. Exposed as a module-level singleton (`settings`). DB lookup is wrapped in `try/except` so the singleton is safe to use at module import time before `init_db()` has run.
- **`core/manager.py`** — Added `get_setting()`, `set_setting()`, and `reset_stuck_tasks()`.
- **`api/routes/settings.py`** — `GET /api/settings` returns all configurable settings with current values. `POST /api/settings` updates a single setting by key+value in the request body (key kept out of the URL path to avoid percent-encoding issues with colons).
- **`frontend/settings.html`** — New Settings page. Renders each setting as a labelled text input with a Save button. Changes confirmed with a toast notification.
- **Nav updated** on all pages to include the Settings link.

#### Migration to Settings Singleton
All modules that previously read from `config.ini` directly at import time have been migrated to read from the `settings` singleton at call time. This means UI changes take effect on the next request without a restart:

| Module | Settings consumed |
|---|---|
| `extractors/ocr_extractor.py` | `tesseract:path` |
| `embeddings/embedder.py` | `ollama:embed_model` |
| `llm/factory.py` | `llm:provider`, `ollama:chat_model`, `ollama:host` |
| `search/semantic.py` | `qdrant:host`, `qdrant:port` |
| `workers/embedding_worker.py` | `qdrant:host`, `qdrant:port` |

#### Worker Controls
- **Reset Stuck Tasks** — `POST /api/workers/reset_stuck` resets any task stuck in `EXTRACTING` or `EMBEDDING` back to `PENDING`. Button added to Dashboard next to Pause/Resume.

#### RAG Quality Improvements
- **Stronger system prompt** — Explicit rules: use only the provided excerpts, hard fallback phrase if context is insufficient, clear delimiters around the context block.
- **Temperature `0.1`** — Set on all Ollama chat calls to reduce hallucination.
- **Think-block accordion** — `<think>...</think>` blocks from DeepSeek R1 models are extracted from the raw response and returned as a separate `thinking` field. The Search page renders a collapsed "▶ Model Thinking" accordion above the answer; expands on click. Non-reasoning models produce no thinking block and the accordion is hidden.
- **Empty context guard** — If no relevant chunks are retrieved, returns a canned "no relevant documents" message instead of sending an empty context to the LLM.

#### Bug Fixes
- **Corrupt `docvault.db`** — Detected and deleted; recreated cleanly on next startup.
- **Import-time DB race condition** — `settings.get()` now catches all DB exceptions and falls through to `config.ini`/defaults, so it is safe to call before `init_db()` runs.
- **Tesseract default path** — Schema default had a literal tab character instead of `\tesseract.exe`. Fixed.
- **`settings.db_path` pointed at `config.ini`** — `core/settings.py` was calling `manager.get_db_path(_config_path)`, passing the config file path as if it were the DB path. Fixed to `manager.get_db_path()` (no argument).
- **Deprecated `qdrant_client.search()`** — `qdrant-client` v1.17 removed `.search()`. Both `embeddings/vector_store.py` (sync) and `search/semantic.py` (async) migrated to `.query_points()` / `await async_client.query_points()`.

---

## 4. Configurable Settings (via UI)

| Key | Label | Default |
|---|---|---|
| `qdrant:host` | Qdrant Host | `localhost` |
| `qdrant:port` | Qdrant Port | `6333` |
| `ollama:host` | Ollama Host | `http://localhost:11434` |
| `ollama:embed_model` | Embedding Model | `nomic-embed-text` |
| `llm:provider` | RAG Provider | `ollama` |
| `ollama:chat_model` | Chat Model | `llama3` |
| `tesseract:path` | Tesseract Path | `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| `paths:scan_directory` | Scan Directory | `.` |

---

## 5. Key Architectural Decisions

- **Qdrant on local Docker** — Moved from NAS to workstation Docker container, reducing write times from ~17s to ~0.5s.
- **Daemon thread workers** — Immediate shutdown on `Ctrl+C`; idempotent pipeline means no data loss on abrupt exit. Tasks stuck mid-flight can be reset via the Dashboard.
- **Settings 3-tier lookup** — DB (user overrides, persisted) → `config.ini` (deployment defaults) → schema default (code fallback). Allows runtime changes without file edits or restarts.
- **LLM think-block separation** — Reasoning model output is split into `answer` and `thinking` fields at the API layer, keeping the UI clean while still exposing the reasoning on demand.

---

## 6. Hardware & Runtime

| Component | Detail |
|---|---|
| Workstation | Windows 11, Ryzen 9 7950X3D, 128 GB RAM, RTX 4090 (24 GB VRAM) |
| Qdrant | Local Docker container on workstation |
| Ollama | Local workstation |
| Embeddings | `nomic-embed-text` via Ollama |
| Whisper | `large-v3`, CUDA (fp16) |
| OCR | Tesseract |

**Recommended chat models (on this hardware):**
- `deepseek-r1:14b` — best balance of speed and quality for RAG (fits fully in VRAM)
- `deepseek-r1:32b` — higher quality, still fits in 24 GB VRAM
- `llama3.2:3b-instruct-q8_0` — fastest option, good for quick queries
- `llama3.1:70b-instruct-q4_K_M` — highest quality, splits across VRAM + RAM (slow)

---

## 7. Known Limitations & Future Work

- **Tesseract and Ollama** must be installed/pulled manually before first run.
- **LLM API key** (for Claude provider) is still read from `config.ini` only — not yet in the Settings UI.
- **Settings changes for workers** (Qdrant host/port, embedding model) take effect on the next task cycle; in-flight tasks use the value that was active when they started.
- **Potential future enhancements:**
  - Re-index / force re-embed button per file in the Catalog
  - Search result score threshold filtering
  - Extractor "manifest" system for per-extractor configuration via UI
  - Claude / Gemini API key management in Settings
