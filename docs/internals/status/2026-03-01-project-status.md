# Project Status & Design Notes — 2026-03-01

This document is the authoritative reference for the current state of the DocVault project. It supersedes the 2026-02-28 status document.

---

## 1. Project Summary

DocVault is a local-first document intelligence platform. It scans a designated folder, extracts text and metadata from a wide variety of file types (PDFs, Office documents, images, audio, video), and makes all content searchable through a web interface. It combines full-text search (SQLite FTS5) with semantic vector search (Qdrant + Ollama embeddings) and supports Retrieval-Augmented Generation (RAG) to answer natural-language questions grounded in document content.

---

## 2. Core Philosophy: Maximum Extraction

DocVault's guiding principle is **maximum extraction**: extract as much information as possible from every file found. No file should be dark.

- Every page of every PDF is OCR'd if it has no text layer (or a sparse one).
- Images embedded in PDFs are described and transcribed by a vision model; descriptions are appended to the parent document's extracted text.
- Standalone images receive a full visual description plus an OCR pass.
- Audio and video are transcribed via Whisper.
- All extracted content feeds both FTS (keyword) and semantic (vector) search indexes.
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
- Serve a web UI for statistics, catalog browsing, hybrid search, RAG chat, and file inspection.
- Persist all configuration at runtime via the Settings page — no restart required for most changes.
- Detect moved or renamed files by content hash and update records in-place without re-embedding.
- Show per-setting help text via a `?` popup on the Settings page.
- Inspect any file's full DB record, extracted text, vector chunks, and extracted images via a modal from the Catalog or Search page.

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

- **Pydantic v2 `null` rejection** — `QueryRequest` fields `file_type`, `date_from`, `date_to` changed from `str = None` to `str | None = None` so JSON `null` values are accepted.
- **Wrong chat model** — `config.ini` had stale `chat_model = llama3`; updated to `deepseek-r1:14b`. Schema default updated to match.
- **ERROR status on partial extraction** — Worker set `ERROR` if any extractor failed, even when other extractors succeeded. Fixed: `final_status = 'ERROR' if (errors and not has_content) else 'EXTRACTED'`.
- **RAG returns nothing for filename queries** — Embedding was computed from chunk text only; filenames had zero semantic signal. Fixed: embedding worker now prepends the filename to each chunk before calling the embedding model. Stored payload remains clean (no prefix). Requires a full reindex to take effect.
- **RAG score threshold too strict** — Default `0.65` threshold filtered out valid results for conversational queries (e.g. "are there any images of people?" scored 0.603 for the correct result). Fixed with a separate `search:rag_threshold` (default `0.50`) used only on the RAG path; the display search threshold is unchanged.

#### Rebuild Vector Index

- **`POST /api/utils/reindex`** — Wipes the Qdrant collection and resets all `COMPLETED` tasks to `EXTRACTED` so the embedding worker re-processes them. Extracted text, FTS index, file metadata, and settings are untouched.
- **Settings → Danger Zone** — Two-step confirmation modal (plain warning → bold red final confirmation) before the wipe is executed. Reports the number of documents queued on completion.

#### Settings — Help Text (`?` Popups)

Every setting in the schema now has a `description` field with a plain-English explanation. The Settings UI renders a small `?` badge next to each label; clicking it opens a floating popup with the description, full key name, and a close button. Descriptions cover purpose, effect, valid values, and any caveats (e.g. "changing this requires re-embedding").

#### Extracted Artefact Cache

PDF-embedded images are no longer saved next to the source document. They are now written to a configurable cache directory:

- **Default:** `<app root>\.cache\extracted_images\`
- **Per-PDF subfolder:** `<pdf_stem>_<md5hash8>` — human-readable name with a short collision-proof suffix.
- **Setting:** `paths:cache_directory` (General tab). Leave blank for the default.
- **Ingestor guard:** The ingestor skips the cache directory during scan walks (via `dirs.clear()`) even if it is configured to be inside the scan tree, preventing an infinite re-ingest loop.

#### DB Inspector Modal

A full file inspection modal is now available from both the Catalog and Search pages.

**Access points:**
- Catalog: right-click any row → **🔍 Inspect**
- Search results: **🔍 Inspect** button alongside Open/Folder
- Chat source citations: **🔍** icon button

**Modal sections:**
| Section | Contents |
|---|---|
| File Record | Status badge, type, size, priority, created/modified/updated dates, full path, SHA-256 hash |
| Error Log | Full error text (red block); hidden if no error |
| Extracted Text | Full extracted content, scrollable, character count |
| Vector Chunks | Count badge + text preview of every indexed chunk |
| Extracted Images | Per-image: filename, page number, dimensions, Open/Folder buttons |

**API:** `GET /api/catalog/inspect?path=<url-encoded-path>` — returns task record, Qdrant chunks, and extracted images. Route declared before `/catalog/{file_hash}` to avoid FastAPI swallowing "inspect" as a hash.

---

## 5. Complete Settings Schema

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

---

## 6. Key Architectural Decisions

### Hash-as-Primary-Key
Every document is identified by the SHA-256 hash of its content. The same file at a different name or location produces the same hash and the same DB record. Move detection is trivial; duplicate files are never re-embedded.

### 3-Tier Settings Lookup
DB override → `config.ini` → schema default. Settings changed via the UI are written to the DB and take effect on the next request or worker cycle without a server restart.

### Filename-Prefixed Embeddings
The embedding worker prepends `<filename>\n` to each chunk text before calling the embedding model. The Qdrant payload stores the clean chunk text. This means filename-based queries ("describe page_084_img_001.png") produce semantically meaningful vectors while keeping the LLM context clean.

### Dual Score Thresholds
Two independent score thresholds serve different purposes:
- `embeddings:score_threshold` (0.65) — display search; high precision, low noise.
- `search:rag_threshold` (0.50) — RAG context retrieval; wider net so the LLM sees borderline-relevant chunks and can judge their relevance itself.

### Multi-Stage PDF Extraction
Pages proceed through increasingly expensive stages and stop as soon as sufficient text is obtained. Fast for text-layer PDFs; thorough for scanned documents.

### Vision Model as Universal Fallback
Any content that text tools cannot read is sent to a multimodal vision model. The shared `extractors/vision.py` utility is called by all extractors that need it.

### Extracted Artefact Cache
PDF-embedded images are saved to a dedicated cache directory rather than next to the source documents. Subfolders are named `<pdf_stem>_<md5hash8>` to be human-readable and collision-proof. The ingestor guards against the cache being inside the scan tree.

### Daemon Thread Workers
Workers run as daemon threads for instant `Ctrl+C` exit. The pipeline is idempotent: interrupted tasks are reset to `PENDING` on restart.

### Hybrid Search — FTS5 + Semantic via RRF
FTS and semantic search run independently; results are merged with Reciprocal Rank Fusion. Cosine score from the semantic result is preserved through the merge and displayed as a confidence badge.

---

## 7. Hardware & Runtime

| Component | Detail |
|---|---|
| Workstation | Windows 11, Ryzen 9 7950X3D, 128 GB RAM, RTX 4090 (24 GB VRAM) |
| Qdrant | Local Docker container |
| Ollama | Local workstation |
| Embeddings | `nomic-embed-text` via Ollama |
| OCR | Tesseract-OCR |
| PDF rendering | pdf2image + Poppler |
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

## 8. Known Limitations & Future Work

| Item | Notes |
|---|---|
| Deleted file detection | Ingestor does not mark files missing when they disappear from disk; records remain in the DB. |
| Claude API key | Read from `config.ini` only; not yet exposed in the Settings UI. |
| Per-extractor configuration | No per-extractor settings (Whisper model, DPI, OCR language) in the UI. |
| Cache migration | Existing images extracted before the cache feature was added remain next to their source PDFs; they are not automatically relocated. Reprocessing the parent PDF will write new copies to the cache. |
| Embedding model change | Changing `ollama:embed_model` invalidates all existing vectors; the Rebuild Index button in Settings → Danger Zone must be used to wipe and re-embed. |
