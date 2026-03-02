# DocVault — Design Document
**Date:** 2026-02-26
**Status:** Approved

---

## Vision

A general-purpose document intelligence suite that can be pointed at any folder.
Catalogs every file, extracts text and content from all known types, flags unknowns,
indexes everything for full-text and semantic search, and exposes a clean web GUI
for control, browsing, and RAG-powered querying.

---

## Project Location

```
E:\DocVault\
```

---

## Folder Structure

```
E:\DocVault\
  core\
    __init__.py
    manager.py            # SQLite DB layer (tasks, FTS5, settings)
    ingestor.py           # Walk folder, SHA256 hash, insert PENDING tasks
    router.py             # Map file extension → extractor list
  extractors\
    __init__.py
    text_extractor.py     # PDF text via pypdf
    image_extractor.py    # PDF embedded images via pypdf + Pillow
    word_extractor.py     # .docx via python-docx
    excel_extractor.py    # .xlsx via openpyxl
    pptx_extractor.py     # .pptx via python-pptx
    plaintext_extractor.py# .txt .md .csv .json .py .log etc.
    ocr_extractor.py      # .jpg .png .tiff via Tesseract
    transcriber.py        # .mp3 .wav .m4a via Whisper large-v3 (CUDA)
    video_extractor.py    # .mp4 .mov .mkv → extract audio → transcriber
    metadata_extractor.py # All media via ffprobe
    unknown_extractor.py  # Flag unsupported types, no crash
  embeddings\
    __init__.py
    chunker.py            # Split extracted text into overlapping chunks
    embedder.py           # Generate vectors via Ollama nomic-embed-text
    vector_store.py       # Push/query Qdrant
  llm\
    __init__.py
    base.py               # Abstract BaseLLMProvider interface
    ollama_provider.py    # Local Ollama (default)
    claude_provider.py    # Anthropic Claude API
    gemini_provider.py    # Google Gemini API
  search\
    __init__.py
    fts.py                # SQLite FTS5 full-text search
    semantic.py           # Qdrant vector similarity search
    hybrid.py             # Merge + re-rank FTS + semantic results
  workers\
    __init__.py
    extraction_worker.py  # Claims PENDING tasks, routes to extractor, writes DB
    embedding_worker.py   # Claims EXTRACTED tasks, chunks, embeds, pushes Qdrant
  api\
    __init__.py
    main.py               # FastAPI app, mounts routes + static files
    routes\
      catalog.py          # GET /catalog, /catalog/{hash}, /catalog/stats
      search.py           # GET /search?q=...&mode=fts|semantic|hybrid
      query.py            # POST /query (RAG: retrieve chunks → LLM → answer)
      workers.py          # GET/POST /workers (status, start, stop, pause)
  frontend\
    index.html            # Dashboard / control panel
    catalog.html          # File browser
    search.html           # Search + RAG query
    static\
      app.js
      styles.css          # Tailwind CSS
  config.ini              # scan_directory, qdrant_host, llm_provider, etc.
  requirements.txt
  run.py                  # Entry point: starts FastAPI + spawns workers
```

---

## File Router

`core/router.py` maps extensions to ordered extractor lists.
Unknown extensions → `unknown_extractor` (flags the file, no crash).
Adding a new type = one new extractor + one line in the router.

| Extension(s) | Extractors |
|---|---|
| `.pdf` | text_extractor, image_extractor |
| `.docx` | word_extractor |
| `.xlsx` | excel_extractor |
| `.pptx` | pptx_extractor |
| `.txt .md .csv .json .py .log .yaml .toml` | plaintext_extractor |
| `.jpg .jpeg .png .tiff .bmp .webp` | ocr_extractor |
| `.mp3 .wav .m4a .flac .ogg` | metadata_extractor, transcriber |
| `.mp4 .mov .mkv .avi .webm` | metadata_extractor, video_extractor |
| anything else | unknown_extractor |

---

## Data Pipeline

### Task Lifecycle

```
PENDING → EXTRACTING → EXTRACTED → EMBEDDING → COMPLETED
               ↓                        ↓
            ERROR                    ERROR
```

- **Extraction worker** claims `PENDING`, runs extractors, writes text to SQLite,
  images to disk, updates status to `EXTRACTED` (or `ERROR`).
- **Embedding worker** claims `EXTRACTED`, chunks text, generates embeddings,
  pushes to Qdrant, updates status to `COMPLETED`.

### SQLite Schema (core tables)

```sql
-- File catalog + extracted content
CREATE TABLE tasks (
    file_hash     TEXT PRIMARY KEY,
    file_path     TEXT NOT NULL,
    file_type     TEXT,               -- extension, e.g. 'pdf'
    status        TEXT DEFAULT 'PENDING',
    worker_id     TEXT,
    extracted_text TEXT,
    error_log     TEXT,
    metadata_json TEXT,
    last_update   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- FTS5 full-text index (auto-updated via trigger)
CREATE VIRTUAL TABLE fts_index USING fts5(
    file_hash UNINDEXED,
    file_path UNINDEXED,
    content,
    content='tasks',
    content_rowid='rowid'
);

-- Extracted images
CREATE TABLE extracted_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_hash  TEXT NOT NULL,
    file_path    TEXT NOT NULL UNIQUE,
    page_num     INTEGER,
    image_index  INTEGER,
    width        INTEGER,
    height       INTEGER,
    extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Worker / app settings (pause flags, config overrides)
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

### Qdrant Schema

Collection: `docvault`
Vector size: 768 (nomic-embed-text)
Distance: Cosine
On-disk: True (saves NAS RAM)

Each point payload:
```json
{
  "file_hash": "abc123",
  "file_path": "E:/Docs/report.pdf",
  "file_type": "pdf",
  "chunk_index": 3,
  "chunk_text": "...500 token chunk..."
}
```

---

## Extractor Interface Contract

Every extractor exposes one function:

```python
def extract(file_path: str) -> tuple[any, str | None]:
    """Returns (result, error). On success: (result, None). On failure: (None, error_str)."""
```

---

## LLM Provider Interface

```python
class BaseLLMProvider:
    def chat(self, messages: list[dict]) -> str: ...
    def embed(self, text: str) -> list[float]: ...
```

Default: `OllamaProvider` (local, no API key needed).
Swap via `config.ini` — zero code changes.

---

## Search

### Full-Text Search (FTS5)
- SQLite FTS5 virtual table over `extracted_text`
- BM25 ranking built in
- Instant, no extra service

### Semantic Search (Qdrant)
- Query embedded via same nomic-embed-text model
- Top-K cosine similarity results from Qdrant
- Returns chunks + source file metadata

### Hybrid Search
- Run both, merge results, deduplicate by source file
- Simple reciprocal rank fusion (RRF) for re-ranking

---

## RAG Query Flow

```
User question
    → embed question (nomic-embed-text)
    → semantic search Qdrant → top 5 chunks
    → optional: FTS5 search → top 3 results
    → build context: [chunk1, chunk2, ...] + question
    → send to LLM provider → answer
    → return answer + source citations
```

---

## GUI — Three Views

### 1. Dashboard (index.html)
- Stats cards: Total files, Extracted, Embedded, Errors, Unknown types
- Worker controls: Start / Stop / Pause each worker independently
- Live activity feed (SSE or polling)
- Folder config: change scan directory without restarting

### 2. Catalog (catalog.html)
- Table of all files with status, type, size, last updated
- Filter by type, status, date
- Click a file → view extracted text, extracted images, metadata
- Re-process button for error/unknown files

### 3. Search & Query (search.html)
- Search bar with mode toggle: Full-text / Semantic / Hybrid
- Results list with file name, type, text snippet, relevance score
- RAG query box: ask a question, get an answer with source citations
- LLM provider selector (Ollama / Claude / Gemini)

---

## Hardware & Dependencies

| Component | Detail |
|---|---|
| Workstation | Windows 11, Ryzen 9 7950X3D, 128GB RAM, RTX 4090 |
| Qdrant | Synology NAS Docker, 192.168.1.11:6333 |
| Ollama | Local workstation |
| Embeddings | nomic-embed-text via Ollama |
| Whisper | large-v3, CUDA (fp16) |
| OCR | Tesseract |

### Key Python Dependencies
```
fastapi, uvicorn          # Web API
pypdf[image]              # PDF extraction
python-docx               # Word
openpyxl                  # Excel
python-pptx               # PowerPoint
pytesseract, Pillow       # OCR
openai-whisper, torch     # Transcription
ffmpeg-python             # Media metadata + video audio extraction
qdrant-client             # Vector store
ollama                    # Local LLM + embeddings
httpx                     # HTTP client for LLM providers
```

---

## Out of Scope (This Phase)

- Docker / nvidia-docker deployment
- PostgreSQL sync
- Multi-user / auth
- Web scraping / document acquisition
- Email / cloud storage connectors (OneDrive, Google Drive)
