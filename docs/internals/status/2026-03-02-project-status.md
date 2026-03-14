# Project Status & Design Notes — 2026-03-02

This document is the authoritative reference for the current state of the DocVault project. It supersedes the 2026-03-01 status document.

---

## 1. Project Summary

DocVault is a local-first document intelligence platform. It scans a designated folder, extracts text and metadata from a wide variety of file types (PDFs, Office documents, images, audio, video, Google Drive stubs), and makes all content searchable through a web interface. It combines full-text search (SQLite FTS5) with semantic vector search (Qdrant + Ollama embeddings) and supports Retrieval-Augmented Generation (RAG) to answer natural-language questions grounded in document content.

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

## 3. Current Status: **Operational**

All core components are in place and the system is fully operational. The application can:

- Ingest files from a target directory on a continuous basis.
- Route different file types to the correct content extractors via a priority-aware router.
- Run extraction and embedding tasks in parallel background daemon workers.
- Apply a three-stage PDF extraction pipeline (native text → render+OCR → vision model).
- Describe and transcribe standalone images and PDF-embedded images via a vision model.
- Transcribe audio and video files via Whisper; describe video frames via vision model.
- Extract content from Google Drive stub files via the Drive API (requires one-time OAuth).
- Serve a web UI for statistics, catalog browsing, search (content + filename), RAG chat, and file inspection.
- Persist all configuration at runtime via the Settings page — no restart required for most changes.
- Detect moved or renamed files by content hash and update records in-place without re-embedding.
- Show per-setting help text via a `?` popup on the Settings page.
- Inspect any file's full DB record, extracted text, vector chunks, and extracted images via a modal from the Catalog or Search page.
- Authorize Google Drive access via a one-click OAuth flow on the Utilities page.

---

## 4. Feature & Fix History

### Sessions 1–2 — 2026-02-26 to 2026-02-28

See the 2026-02-28 status document for detailed notes on:
- Scalable catalog UI with pagination and sorting
- Continuous ingestion worker, task prioritisation, daemon threads
- Settings system (3-tier lookup: DB → config.ini → schema default)
- RAG quality improvements (system prompt, temperature, thinking accordion, conversation history)
- Qdrant v1.17 migration (`query_points`)

---

### Session 3 — 2026-03-01 (morning)

#### Search & UI Improvements

- **Score threshold** — Qdrant drops results below a configurable minimum cosine similarity (default `0.65`). Eliminates "best of a bad lot" results where no genuinely relevant chunks exist.
- **Score badge** — Confidence percentage displayed next to each result filename. Green ≥80%, yellow ≥65%.
- **Smaller chunks** — Default chunk size reduced from 2000/200 to 600 chars / 100 overlap for more precise semantic matching.
- **Hybrid merge fix** — Semantic payload (with cosine score) preferred over FTS payload in merge step.
- **Open File / Open Folder** — Right-click context menu on catalog rows; inline buttons on search results and chat source citations.
- **Chat source citations** — Each RAG answer lists deduplicated source files with Open/Folder buttons.
- **Date range and file type filters** — Both the search bar and the Ask chat accept `file_type`, `date_from`, `date_to` constraints. FTS uses SQL JOINs; semantic search pre-queries SQLite for matching hashes and passes them as a Qdrant `MatchAny` filter.

#### PDF Extraction — Multi-Stage Pipeline

| Stage | Method | Condition |
|---|---|---|
| 1 | pypdf native text layer | Always attempted first |
| 2 | pdf2image (300 DPI) + Tesseract `--psm 1` | Page text below `pdf:sparse_threshold` (default 50 chars) |
| 3 | Ollama vision model (minicpm-v) | Tesseract returns nothing or is insufficient |

#### Vision Model — Image Description

- **`extractors/vision.py`** — Shared vision utility called by all extractors that need image description.
- **Standalone images**: vision model provides full description and text transcription; Tesseract is fallback.
- **PDF embedded images**: described in-memory during extraction; descriptions appended to parent document text as `[Image — page N]\n<description>`.

#### File Metadata & Move Detection

- `tasks` table extended with `file_size`, `file_created`, `file_modified` columns (auto-migrated on startup).
- Ingestor stores `os.stat()` metadata on every new insert.
- Move detection: same SHA-256 hash at a new path → update DB record and Qdrant vector payloads in-place via `set_payload`. No re-extraction or re-embedding required.

#### Settings Expansion

New groups added: PDF, Embeddings, Search, Vision. Every setting now has a `description` field in the schema, surfaced via the API and rendered as a `?` popup in the Settings UI.

---

### Session 4 — 2026-03-01 (afternoon)

#### Bug Fixes

- **Pydantic v2 `null` rejection** — `QueryRequest` fields changed from `str = None` to `str | None = None`.
- **Wrong chat model** — `config.ini` had stale `chat_model = llama3`; updated to `deepseek-r1:14b`.
- **ERROR status on partial extraction** — Worker set `ERROR` if any extractor failed, even when others succeeded. Fixed: `final_status = 'ERROR' if (errors and not has_content) else 'EXTRACTED'`.
- **RAG returns nothing for filename queries** — Embedding worker now prepends the filename to each chunk before calling the embedding model. Requires a full reindex to take effect.
- **RAG score threshold too strict** — Added separate `search:rag_threshold` (default `0.50`) used only on the RAG path.

#### Rebuild Vector Index

- **`POST /api/utils/reindex`** — Wipes the Qdrant collection and resets all `COMPLETED` tasks to `EXTRACTED`.
- **Settings → Danger Zone** — Two-step confirmation modal before the wipe is executed.

#### Settings — Help Text (`?` Popups)

Every setting in the schema has a `description` field. The Settings UI renders a `?` badge next to each label; clicking it opens a floating popup with the full explanation.

#### Extracted Artefact Cache

PDF-embedded images are written to a configurable cache directory:
- **Default:** `<app root>\.cache\extracted_images\`
- **Per-PDF subfolder:** `<pdf_stem>_<md5hash8>`
- **Setting:** `paths:cache_directory` (General tab)
- **Ingestor guard:** The ingestor skips the cache directory during scan walks via `dirs.clear()`.

#### DB Inspector Modal

Accessible from Catalog (right-click) and Search (🔍 Inspect button / 🔍 icon on chat sources).
- **API:** `GET /api/catalog/inspect?path=<url-encoded-path>`
- **Sections:** File Record, Error Log, Extracted Text, Vector Chunks, Extracted Images.
- **Route ordering:** `/catalog/inspect` declared before `/catalog/{file_hash}` to avoid collision.

#### Video Frame Description

`extractors/video_extractor.py` rewritten:
- ffmpeg extracts one PNG frame every N seconds (`video:frame_interval`, default 10s).
- Each frame is described by the vision model (`vision:model`).
- Output: `[Audio Transcript]\n<whisper text>\n\n[Visual Content]\n[Frame @ M:SS]\n<description>`
- New settings: `video:describe_frames` (true/false), `video:frame_interval` (seconds).

#### Settings DB Separation

Settings moved from `docvault.db` to a separate `settings.db` so user configuration survives data resets.
- `manager.get_settings_db_path()` returns the path.
- `manager.init_settings_db()` is called on startup before `init_db()`.
- `get_setting`, `set_setting`, `get_pause_state`, `set_pause_state` — all use `get_settings_db_path()` internally; no `db_path` parameter.

#### Google Drive Extractor

`extractors/gdrive_extractor.py` handles `.gdoc`, `.gsheet`, `.gslides`, `.gform`, `.gdraw` stub files:
- Reads the `doc_id` from the stub JSON.
- Calls the Drive API `files().export_media()` to download to a temp file.
- Hands the temp file to the appropriate existing extractor (word/excel/pptx/plaintext/text+image).
- OAuth: Desktop app flow via `google-auth-oauthlib`. Token saved to `google_token.json`.
- Config: `google:credentials_path`, `google:token_path` settings (Google tab in Settings UI).
- Credentials file: `E:\DocVault\credentials\google_credentials.json` (in `.gitignore`).

---

### Session 5 — 2026-03-02

#### Google Drive Authorization UI

Added a one-click authorization flow to the Utilities page:

- **`GET /api/utils/gdrive_status`** — Returns `{authorized: bool}` by checking whether the token file exists.
- **`POST /api/utils/gdrive_authorize`** — Runs the OAuth flow (opens a browser window), saves the token, returns `{ok: true}` or `{ok: false, detail: '...'}`.
- **Utilities page card** — Shows current auth status ("Authorised" / "Not authorised") on load. "Authorise Google Drive" button triggers the flow and updates the badge in-place on success.

> **Important:** New API endpoints require a **server restart** before they are available. A 404 with `{"detail":"Not Found"}` always means the running server predates the new route — just restart.

#### Three-Tab Search Page

`frontend/search.html` restructured into three tabs: **Search** | **Filename** | **Ask**.

**Search tab** — existing content search, unchanged. Hybrid / FTS / Semantic mode selector, file-type and date filters, score badges.

**Filename tab** — new. Searches the `file_path` column in SQLite using multi-token substring matching:
- Space-separated tokens all must match (AND logic). `"report 2024"` → files with both words in the path.
- Searches full path (catches folder names too).
- Results show: filename (bold), full path, type · size · modified date. Non-COMPLETED files show a status badge.
- Supports same file-type and date filters as other tabs.
- **API:** `GET /api/search/filename?q=...&file_type=...&date_from=...&date_to=...`
- **Backend:** `manager.filename_search()` in `core/manager.py`.

**Ask tab** — existing RAG chat, unchanged. Filters, conversation history, thinking accordion, source citations.

URL hash deep-linking: `/search#filename` or `/search#ask` opens the named tab directly.

---

## 5. Complete File Map

```
E:\DocVault\
├── run.py                          Entry point — FastAPI + worker threads
├── config.ini                      Machine-specific defaults (all values set)
├── docvault.db                     SQLite: tasks, FTS5, extracted_images
├── settings.db                     SQLite: settings key/value + pause state (separate from data)
│
├── api/
│   ├── main.py                     FastAPI app, router mounts, static + page routes
│   └── routes/
│       ├── catalog.py              GET /catalog, GET /catalog/inspect, GET /catalog/{hash}
│       ├── search.py               GET /search, GET /search/filename
│       ├── query.py                POST /query (RAG)
│       ├── workers.py              GET/POST /workers (pause/resume/stats)
│       ├── utils.py                GET /utils/qdrant_check, POST /utils/reindex,
│       │                           GET /utils/gdrive_status, POST /utils/gdrive_authorize,
│       │                           POST /utils/open_path
│       └── settings.py             GET /settings, POST /settings
│
├── core/
│   ├── ingestor.py                 Walks scan dir, detects new+moved files, skips cache dir
│   ├── manager.py                  SQLite layer: insert_task, fts_search, filename_search, …
│   ├── router.py                   ROUTES dict: ext → [extractors], PRIORITIES dict
│   └── settings.py                 Settings singleton (3-tier lookup)
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
│   ├── extraction_worker.py        Polls PENDING tasks, runs extractors, writes FTS
│   ├── embedding_worker.py         Polls EXTRACTED tasks, chunks, embeds, writes Qdrant
│   └── utils.py                    Shared worker helpers (pause check, worker_id)
│
├── frontend/
│   ├── index.html                  Dashboard (stats, stuck-task reset)
│   ├── catalog.html                Paginated file catalog + right-click context menu + inspect
│   ├── search.html                 Three-tab: Search | Filename | Ask
│   ├── utils.html                  Qdrant health check + Google Drive authorization
│   ├── settings.html               Tabbed settings UI + Danger Zone (rebuild index)
│   └── static/
│       ├── app.js                  Shared JS: api(), formatPath(), openFile/Folder, inspectFile()
│       └── styles.css              Status badge colours
│
├── credentials/                    OAuth credentials (in .gitignore)
│   ├── google_credentials.json     Desktop app OAuth client secret
│   └── google_token.json           Saved OAuth token (created after first authorization)
│
└── docs/
    ├── plans/                      Original design and implementation plans
    └── status/
        ├── 2026-02-28-project-status.md
        ├── 2026-03-01-project-status.md
        └── 2026-03-02-project-status.md   ← this file
```

---

## 6. Complete Settings Schema

| Key | Default | Group | Purpose |
|---|---|---|---|
| `paths:scan_directory` | `.` | General | Root folder to monitor for documents |
| `paths:cache_directory` | *(empty = app/.cache/extracted_images)* | General | Folder for extracted artefacts (PDF images) |
| `llm:provider` | `ollama` | General | LLM backend (`ollama` or `claude`) |
| `tesseract:path` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | General | Tesseract binary path |
| `ollama:host` | `http://localhost:11434` | Ollama | Ollama server URL |
| `ollama:chat_model` | `deepseek-r1:14b` | Ollama | Model for RAG chat |
| `ollama:embed_model` | `nomic-embed-text` | Ollama | Model for embeddings (changing requires full reindex) |
| `ollama:num_ctx` | `8192` | Ollama | Context window (tokens) |
| `ollama:temperature` | `0.1` | Ollama | Sampling temperature (0 = precise) |
| `ollama:num_predict` | `-1` | Ollama | Max tokens to generate (-1 = unlimited) |
| `ollama:top_p` | `0.9` | Ollama | Nucleus sampling threshold |
| `ollama:repeat_penalty` | `1.1` | Ollama | Repetition penalty |
| `qdrant:host` | `localhost` | Qdrant | Qdrant host |
| `qdrant:port` | `6333` | Qdrant | Qdrant port |
| `pdf:poppler_path` | *(empty)* | PDF | Poppler bin dir (required for pdf2image on Windows) |
| `pdf:sparse_threshold` | `50` | PDF | Chars below which a page is treated as scanned |
| `vision:model` | `minicpm-v` | Vision | Ollama vision model for image description |
| `vision:describe_images` | `true` | Vision | Enable/disable vision description |
| `embeddings:chunk_size` | `600` | Embeddings | Chunk size in characters |
| `embeddings:chunk_overlap` | `100` | Embeddings | Overlap between consecutive chunks |
| `embeddings:score_threshold` | `0.65` | Embeddings | Min cosine similarity for search display results |
| `search:rag_top_k` | `5` | Search | Chunks fed to LLM per RAG query |
| `search:rag_threshold` | `0.50` | Search | Min cosine similarity for RAG context (lower = wider net) |
| `search:result_limit` | `20` | Search | Max results per search query |
| `search:fts_weight` | `0.4` | Search | FTS weight in hybrid RRF merge |
| `search:sem_weight` | `0.6` | Search | Semantic weight in hybrid RRF merge |
| `video:describe_frames` | `true` | Video | Enable ffmpeg frame extraction + vision description |
| `video:frame_interval` | `10` | Video | Seconds between sampled frames |
| `google:credentials_path` | *(empty)* | Google | Path to OAuth client credentials JSON |
| `google:token_path` | *(empty)* | Google | Path where OAuth token is saved |

---

## 7. Key Architectural Decisions

### Hash-as-Primary-Key
Every document is identified by the SHA-256 hash of its content. The same file at a different name or location produces the same hash and the same DB record. Move detection is trivial; duplicate files are never re-embedded.

### 3-Tier Settings Lookup
DB override → `config.ini` → schema default. Settings changed via the UI are written to `settings.db` and take effect on the next request or worker cycle without a server restart.

### Separated settings.db
User configuration lives in `settings.db`, separate from `docvault.db`. This means wiping the document database (e.g. to start fresh) does not lose settings.

### Filename-Prefixed Embeddings
The embedding worker prepends `<filename>\n` to each chunk text before calling the embedding model. The Qdrant payload stores the clean chunk text. Filename-based queries produce semantically meaningful vectors while keeping the LLM context clean.

### Dual Score Thresholds
Two independent score thresholds serve different purposes:
- `embeddings:score_threshold` (0.65) — display search; high precision, low noise.
- `search:rag_threshold` (0.50) — RAG context retrieval; wider net so the LLM sees borderline-relevant chunks.

### Multi-Stage PDF Extraction
Pages proceed through increasingly expensive stages and stop as soon as sufficient text is obtained.

### Vision Model as Universal Fallback
Any content that text tools cannot read is sent to a multimodal vision model. The shared `extractors/vision.py` utility is called by all extractors that need it.

### Extracted Artefact Cache
PDF-embedded images are saved to a dedicated cache directory. Subfolders named `<pdf_stem>_<md5hash8>`. Ingestor guards against the cache being inside the scan tree via `dirs.clear()`.

### Daemon Thread Workers
Workers run as daemon threads for instant `Ctrl+C` exit. The pipeline is idempotent: interrupted tasks are reset to `PENDING` on restart.

### Hybrid Search — FTS5 + Semantic via RRF
FTS and semantic search run independently; results are merged with Reciprocal Rank Fusion. Cosine score from the semantic result is preserved through the merge.

### Google Drive — Export-and-Delegate
Google Drive stub files (`.gdoc`, etc.) contain only a `doc_id`. The extractor fetches the file from the Drive API in an open format (DOCX, XLSX, PDF, …) to a temp file, then delegates to the appropriate existing extractor. No new extraction logic needed; supports all Drive Workspace types.

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

**Recommended chat models:**

| Model | Notes |
|---|---|
| `deepseek-r1:14b` | Best speed/quality balance; fits fully in VRAM |
| `deepseek-r1:32b` | Higher quality; still fits in 24 GB VRAM |
| `llama3.1:70b-instruct-q4_K_M` | Highest quality; splits VRAM + RAM (slower) |
| `llama3.2:3b-instruct-q8_0` | Fastest; good for quick interactive queries |

---

## 9. Known Limitations & Future Work

| Item | Notes |
|---|---|
| Multiple scan roots | Only one `paths:scan_directory` is supported. Users with documents in multiple locations (e.g. `E:\My Docs` and `D:\My Writing`) cannot watch both simultaneously. |
| Deleted file detection | Ingestor does not mark files as missing when they disappear from disk. |
| Claude API key | Read from `config.ini` only; not exposed in the Settings UI. |
| Per-extractor configuration | No per-extractor settings (Whisper model, DPI, OCR language) in the UI. |
| Cache migration | Images extracted before the cache feature was added remain next to source PDFs. Reprocessing the parent PDF writes new copies to the cache. |
| Embedding model change | Changing `ollama:embed_model` invalidates all existing vectors; use Settings → Danger Zone → Rebuild Index. |
| Google Drive first run | Requires one-time OAuth via Utilities → Authorise Google Drive. Token is then reused silently. |
| Google Drive scope | `drive.readonly` scope must be added in Google Cloud Console → APIs & Services → OAuth consent screen. |
| 24/7 operation | Currently runs as a foreground process. No auto-restart, no start-on-boot. A Docker Compose setup or Windows Service wrapper is needed for unattended production use. `Dockerfile` and `docker-compose.yml` exist but are not fully wired. |
| Security hardening | No API authentication — the web UI is open to anyone who can reach the port. Needs token auth or local-only binding for networked deployments. No input validation, rate limiting, file size caps, or extraction timeouts to guard against malformed/malicious files. |
| GitHub release | Not yet packaged for public sharing. Needs: LICENSE, clean .gitignore audit, deep README with screenshots, INSTALL.md (Windows + Docker paths), REQUIREMENTS.md (all external deps), USAGE.md (full user manual). Do this last, after all features are baked. |
| Backup | No backup mechanism for the datastores (`docvault.db`, `settings.db`, Qdrant vectors). A crash or accidental wipe loses all extracted text and embeddings. Needs scheduled backup to a user-defined location and a restore workflow in the UI. |
| Resilience | Workers use `print()` for errors, not a structured logger. No rotating log file. No dead-letter queue for tasks that fail repeatedly. Graceful shutdown (drain in-progress tasks) is not implemented — daemon threads exit immediately on `Ctrl+C`. |
