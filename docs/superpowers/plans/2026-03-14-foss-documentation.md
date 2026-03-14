# FOSS Documentation & Packaging Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare DocVault for public open-source release on GitHub by producing a complete documentation layer and repository hygiene pass — no code changes.

**Architecture:** All work is Markdown authoring and file reorganization. Internal planning docs move to `docs/internals/`. New user-facing docs land in `docs/` and root. Each task is one file; each file is a standalone commit. The spec lives at `docs/superpowers/specs/2026-03-14-foss-documentation-design.md`.

**Tech Stack:** GitHub-Flavoured Markdown, Apache 2.0 license text.

---

## File Map

### Create (new files)
| File | Purpose |
|------|---------|
| `LICENSE` | Apache 2.0 full text |
| `README.md` | Hero doc — capability-first, quickstart, hardware tiers |
| `CONTRIBUTING.md` | How to write a kernel, PR process, conventions |
| `docs/architecture.md` | System overview, data pipeline, component map |
| `docs/setup.md` | First-time setup: native Windows + Docker paths |
| `docs/configuration.md` | Every config key, group, default, description |
| `docs/troubleshooting.md` | Common first-run failures and fixes |
| `docs/extractors/contract.md` | The kernel contract (MANIFEST, BaseExtractor, IngestResult) |
| `docs/extractors/writing-an-extractor.md` | Step-by-step worked example |
| `docs/extractors/catalogue.md` | All built-in extractors with file types and install requirements |
| `docs/tools/test-kernel.md` | Expanded from `docs/test-kernel-cli.md` |
| `docs/tools/build-art-index.md` | Art index seeding and extension guide |
| `docs/internals/plans/PLANS_INDEX.md` | Status-tagged index of all planning docs |

### Move (reorganize)
| Source | Destination |
|--------|------------|
| `docs/plans/` | `docs/internals/plans/` |
| `docs/status/` | `docs/internals/status/` |
| `docs/SideQuests.md` | `docs/internals/SideQuests.md` |
| `docs/RAGUpdate.md` | `docs/internals/RAGUpdate.md` |
| `docs/vision.md` | `docs/internals/vision.md` |
| `docs/test-kernel-cli.md` | Replaced by `docs/tools/test-kernel.md`, original removed |

### Directories to create
- `docs/extractors/`
- `docs/tools/`
- `docs/internals/plans/`
- `docs/internals/status/`

---

## Chunk 1: Foundation — Reorganization + License

### Task 1: Reorganize docs/internals/

**Files:**
- Move: `docs/plans/` → `docs/internals/plans/`
- Move: `docs/status/` → `docs/internals/status/`
- Move: `docs/SideQuests.md` → `docs/internals/SideQuests.md`
- Move: `docs/RAGUpdate.md` → `docs/internals/RAGUpdate.md`
- Move: `docs/vision.md` → `docs/internals/vision.md`

- [ ] **Step 1: Create the internals directory structure**

  ```bash
  mkdir -p docs/internals/plans docs/internals/status docs/extractors docs/tools
  ```

- [ ] **Step 2: Move plans and status dirs**

  ```bash
  git mv docs/plans/* docs/internals/plans/
  git mv docs/status/* docs/internals/status/
  ```

- [ ] **Step 3: Move remaining internal docs**

  ```bash
  git mv docs/SideQuests.md docs/internals/SideQuests.md
  git mv docs/RAGUpdate.md docs/internals/RAGUpdate.md
  git mv docs/vision.md docs/internals/vision.md
  ```

- [ ] **Step 4: Remove now-empty source dirs**

  ```bash
  git rm -r docs/plans docs/status
  ```

  > If the directories don't exist after the moves, `git rm` may show warnings — that's fine, skip it.

- [ ] **Step 5: Update CLAUDE.md references**

  Read `CLAUDE.md`. Find every occurrence of the old paths and update them:

  | Find | Replace with |
  |------|-------------|
  | `docs/status/2026-03-05-project-status.md` | `docs/internals/status/2026-03-05-project-status.md` |
  | `docs/plans/2026-03-02-future-architecture.md` | `docs/internals/plans/2026-03-02-future-architecture.md` |
  | `docs/plans/2026-02-26-docvault-design.md` | `docs/internals/plans/2026-02-26-docvault-design.md` |
  | `docs/plans/2026-02-26-docvault-implementation.md` | `docs/internals/plans/2026-02-26-docvault-implementation.md` |

  Also check the "On Every Session Start" section heading and any other bare `docs/plans/` or `docs/status/` path fragments — replace them with the `docs/internals/` equivalents.

- [ ] **Step 6: Commit**

  ```bash
  git add -A
  git commit -m "chore: move internal planning docs to docs/internals/"
  ```

---

### Task 2: PLANS_INDEX.md

**Files:**
- Create: `docs/internals/plans/PLANS_INDEX.md`

- [ ] **Step 1: Write PLANS_INDEX.md**

  The index must list every file in `docs/internals/plans/` with a status tag. Tag logic:
  - `[ACTIVE]` — plan is current, work is ongoing or imminent
  - `[PARTIAL]` — partially implemented; remaining work tracked in task list
  - `[COMPLETE]` — fully implemented

  Active/Partial plans as of 2026-03-14:
  - `2026-03-04-security-hardening.md` → `[PARTIAL]` (disk sensor, bind address tasks remain)
  - `2026-03-05-prompt-injection-hardening.md` → `[ACTIVE]`
  - `2026-03-07-art-collection-intelligence.md` → `[PARTIAL]`
  - `2026-03-13-archive-xray-implementation.md` → `[ACTIVE]`
  - `2026-03-13-kml-extractor-implementation.md` → `[ACTIVE]`
  - `2026-03-13-ollama-circuit-breaker-implementation.md` → `[ACTIVE]`

  All others → `[COMPLETE]`

  Write the file with this structure:

  ```markdown
  # Plans Index

  Status tags: `[ACTIVE]` in-progress · `[PARTIAL]` partially done · `[COMPLETE]` fully implemented

  ## Active & In-Progress

  | Plan | Status |
  |------|--------|
  | [Archive X-Ray Extractor](2026-03-13-archive-xray-implementation.md) | [ACTIVE] |
  | [KML/KMZ Extractor](2026-03-13-kml-extractor-implementation.md) | [ACTIVE] |
  | [Ollama Circuit Breaker](2026-03-13-ollama-circuit-breaker-implementation.md) | [ACTIVE] |
  | [Prompt Injection Hardening](2026-03-05-prompt-injection-hardening.md) | [ACTIVE] |
  | [Art Collection Intelligence](2026-03-07-art-collection-intelligence.md) | [PARTIAL] |
  | [Security Hardening](2026-03-04-security-hardening.md) | [PARTIAL] |

  ## Completed

  | Plan | Status |
  |------|--------|
  | [Infrastructure Migration](2026-03-03-infrastructure-migration-plan.md) | [COMPLETE] |
  | [Original Implementation](2026-02-26-docvault-implementation.md) | [COMPLETE] |
  | [Future Architecture](2026-03-02-future-architecture.md) | [COMPLETE] |
  ```

  Include design docs (`*-design.md`) as companion rows next to their plan, or omit them if the index grows cluttered — keep it readable.

- [ ] **Step 2: Commit**

  ```bash
  git add docs/internals/plans/PLANS_INDEX.md
  git commit -m "docs: add PLANS_INDEX.md with active/partial/complete tags"
  ```

---

### Task 3: LICENSE

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Write Apache 2.0 LICENSE**

  Fetch the canonical Apache 2.0 text from memory — do not paraphrase. The file starts with:

  ```
  Apache License
  Version 2.0, January 2004
  http://www.apache.org/licenses/
  ```

  The `Copyright` line in the preamble:
  ```
  Copyright [yyyy] [name of copyright owner]
  ```
  Replace with the current year and owner:
  ```
  Copyright 2026 DocVault Contributors
  ```

  Write the complete Apache 2.0 text verbatim. It is a well-known, fixed text — do not omit or summarize any section.

- [ ] **Step 2: Verify**

  Confirm the file starts with `Apache License` and ends with `limitations under the License.`

- [ ] **Step 3: Commit**

  ```bash
  git add LICENSE
  git commit -m "chore: add Apache 2.0 license"
  ```

---

## Chunk 2: Root Documentation — README + CONTRIBUTING

### Task 4: README.md

**Files:**
- Create: `README.md`

Follow the README structure from the spec exactly (sections in this order):

1. Badge row
2. One-line description
3. Hero screenshot placeholder
4. Feature highlights
5. Hardware requirements (two-tier table)
6. Quickstart (native + Docker side by side)
7. What gets extracted (file-type table)
8. Architecture overview (two paragraphs + link)
9. Writing extractors (two sentences + link)
10. Management scripts
11. Contributing
12. License

- [ ] **Step 1: Write badge row and description**

  ```markdown
  # DocVault

  [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
  [![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Docker-lightgrey)](docs/setup.md)

  A local-first document intelligence platform. DocVault scans your files, extracts
  every meaningful signal — text, metadata, faces, speech, code structure, EXIF — and
  makes it all searchable through full-text, semantic, and hybrid search, with an
  optional RAG interface powered by your local Ollama models.
  ```

- [ ] **Step 2: Write hero screenshot placeholder and feature highlights**

  Placeholder:
  ```markdown
  [Screenshot: Search results — coming soon]
  ```

  Feature highlights — group into categories:
  - **Extraction** — Text (PDF, Word, Excel, PowerPoint, plaintext, code), OCR (Tesseract), Vision AI (Ollama), Audio transcription (Whisper), EXIF & media metadata, Face detection & emotion analytics, Archive cataloguing
  - **Search** — Full-text search (FTS5), Semantic search (Qdrant), Hybrid FTS + semantic, RAG (ask questions, get cited answers), Filename search with wildcard/regex
  - **Management** — Multi-vault support, Extractor Lab UI, Real-time resource governor, Hardware telemetry dashboard, Identity Hub (face search)
  - **Local-first** — All processing on your machine. No cloud calls unless you configure them. Your data stays yours.

- [ ] **Step 3: Write hardware requirements table**

  Two-tier table followed by three callout note blocks. The spec's Hardware Framing section (lines 42-57) prescribes: honest-expectations narrative, NVIDIA note, and wmi note — these are the three blocks. Place them directly beneath the table as a natural continuation of the hardware requirements section:

  | | Minimum | Recommended |
  |--|---------|-------------|
  | **CPU** | Any modern x86-64 | Ryzen 9 / i9 class |
  | **RAM** | 8 GB | 32 GB+ |
  | **GPU** | None (CPU-only Ollama) | NVIDIA 8 GB+ VRAM |
  | **Storage** | 20 GB free | SSD, 100 GB+ |
  | **OS** | Windows 10/11, Linux, macOS | Windows 11 (reference) |

  Add a note block after the table:
  > **Honest expectations:** DocVault was built and tuned on a Ryzen 9 7950X3D / 128 GB RAM / RTX 4090. On the minimum tier, text extraction, FTS, and basic RAG work well. Vision AI (image description, face detection) and audio transcription (Whisper) run on CPU but are slow — minutes per file, not seconds. A GPU is the single biggest performance upgrade.

  Add NVIDIA note:
  > **NVIDIA GPU:** `nvidia-ml-py` is used for GPU temperature and utilisation monitoring. On non-NVIDIA hardware, the sensor fails silently and the system continues normally.

  Add Windows-only note:
  > **Windows WMI:** CPU temperature sensing uses the `wmi` package (Windows only). On Linux/macOS — including inside Docker — the WMI sensor is replaced by a no-op stub automatically.

- [ ] **Step 4: Write quickstart (two paths)**

  Present as two columns with a horizontal rule separator:

  **Native (Windows)**
  ```powershell
  git clone https://github.com/your-org/docvault.git
  cd docvault
  .\setup.ps1      # one-time: venv, config, Ollama models, Qdrant
  .\start.ps1      # every time: checks dependencies, starts app
  ```
  Then open `http://localhost:8000`

  **Docker (Linux / macOS / Windows)**
  ```bash
  git clone https://github.com/your-org/docvault.git
  cd docvault
  docker compose up
  ```
  Then open `http://localhost:8000`

  Note: The Docker path runs the Qdrant vector database only. DocVault itself runs natively (Python). `wmi` is not required inside Docker.

- [ ] **Step 5: Write "What gets extracted" table**

  | File type | Extensions | What DocVault extracts |
  |-----------|-----------|------------------------|
  | PDF | `.pdf` | Full text (pdfminer), embedded images (dispatched to OCR/vision), metadata |
  | Word | `.docx` | Text, paragraph structure, metadata |
  | Excel | `.xlsx`, `.xls` | Cell text, sheet names, metadata |
  | PowerPoint | `.pptx` | Slide text, speaker notes, metadata |
  | Plaintext | `.txt`, `.md`, `.rst`, `.log`, `.csv` | Full text |
  | Source code | `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.h`, `.cs`, `.rb`, `.php`, `.sh`, `.sql`, and more | Code text, language, structure analysis |
  | Images | `.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`, `.tiff`, `.webp` | OCR text, AI visual description, EXIF metadata, face detection |
  | Audio | `.mp3`, `.flac`, `.wav`, `.ogg`, `.aac`, `.m4a`, `.opus` | Whisper transcription, ID3/metadata tags |
  | Video | `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm` | Frame-sampled visual description, embedded audio transcription, technical metadata |
  | Archives | `.zip`, `.tar`, `.gz`, `.7z`, `.rar` | File manifest, nested file types, total size |
  | Google Drive | `.gdoc`, `.gsheet`, `.gslides` | Exported text via Google API |

- [ ] **Step 6: Write architecture overview, extractor link, scripts, contributing, license**

  **Architecture overview** (two paragraphs):
  > DocVault runs three daemon workers: an extraction worker (reads files, runs kernels), an embedding worker (chunks text, upserts to Qdrant), and an art enrichment worker (identifies artworks in images). All workers share a SQLite task queue (`docvault.db`). A resource governor monitors CPU, GPU, and disk, throttling workers when the system is under pressure.
  >
  > Three databases keep concerns separate: `docvault.db` (tasks and extracted content, safe to delete and rebuild), `settings.db` (configuration and API keys, survives resets), and `logs.db` (timings, worker errors, system stats). Qdrant handles semantic vectors. See [docs/architecture.md](docs/architecture.md) for the full component map and data-flow diagram.

  **Writing extractors** (two sentences + link):
  > DocVault's extraction system is built on a kernel contract: a Python file with a `MANIFEST` dict and a typed `extract()` function. Any file matching that contract can be dropped into `extractors/` and will be discovered, certified, and routed automatically. See [docs/extractors/contract.md](docs/extractors/contract.md) to get started.

  **Management scripts**:
  ```
  .\setup.ps1   One-time setup: venv, config.ini, Ollama model pulls, Qdrant container
  .\start.ps1   Start the system: checks Docker, Qdrant, Ollama, then runs run.py
  .\reset.ps1   Clean slate: deletes docvault.db and Qdrant vectors; preserves settings.db
  ```

  **Contributing**: Link to [CONTRIBUTING.md](CONTRIBUTING.md).

  **License**: `Apache 2.0 — see [LICENSE](LICENSE).`

- [ ] **Step 7: Commit**

  ```bash
  git add README.md
  git commit -m "docs: add README.md with quickstart, hardware tiers, feature table"
  ```

---

### Task 5: CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

Follow the CONTRIBUTING.md structure from the spec:

1. Welcome + philosophy
2. Development setup
3. The three-worker pipeline
4. Writing a new extractor
5. PR conventions
6. Code conventions
7. Known limitations / side quests

- [ ] **Step 1: Write welcome + philosophy + development setup**

  **Welcome:**
  > DocVault aims for *maximum extraction* — every file should yield every meaningful signal it contains, processed locally, with no data leaving the machine unless the user explicitly configures it. Contributions that deepen extraction, improve search quality, or harden the system are most welcome.

  **Development setup:**
  ```powershell
  git clone https://github.com/your-org/docvault.git
  cd docvault
  .\setup.ps1          # creates venv + installs requirements.txt
  # OR manually:
  python -m venv venv
  venv\Scripts\pip install -r requirements.txt
  ```

  Running tests:
  ```bash
  python -m pytest tests/ -v
  ```

  The test suite does not require a running server, Qdrant, or Ollama. All integration checks in `check_*.py` require the server.

- [ ] **Step 2: Write the three-worker pipeline section**

  Explain what each worker does and what belongs at each level:

  **Extraction worker** (`workers/extraction_worker.py`): Claims a `PENDING` task, picks the highest-priority extractor from the router, calls `extractor.run(file_path, ctx)`, writes text + metadata + images to `docvault.db`, sets task to `EXTRACTED`.

  **Embedding worker** (`workers/embedding_worker.py`): Claims an `EXTRACTED` task, chunks the text, calls Ollama for embeddings, upserts vectors to Qdrant, sets task to `EMBEDDED`.

  **Art enrichment worker** (`workers/art_worker.py`): Runs vision AI against images to identify artworks, enriches the Qdrant payload with art metadata.

  **What belongs where:**
  - Extractor level: reading the file, parsing structure, OCR, calling vision APIs, emitting `IngestResult`
  - Worker level: task state management, Qdrant upsert, priority scheduling, error recovery

  Link to [docs/architecture.md](docs/architecture.md) for depth.

- [ ] **Step 3: Write extractor, PR, code conventions, side quests**

  **Writing a new extractor** — key rules:
  - Must have a `MANIFEST` dict with `id`, `version`, `name`, `extensions` keys
  - Must have `extract(file_path: str, ctx: ExtractorContext) -> tuple` with exact type annotations
  - Return `(IngestResult, None)` on success; `(None, "error message")` on failure
  - Never read settings at import time — use `ctx.settings.get(group, key)` inside `extract()`
  - Check `ctx.cancel_token` periodically in long-running operations
  - Link to full guide: [docs/extractors/writing-an-extractor.md](docs/extractors/writing-an-extractor.md)

  **PR conventions:**
  - Commit messages: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:` prefixes
  - Every new extractor needs: a `MANIFEST`, typed `extract()`, entry in the catalogue doc
  - Bug fixes need a test that reproduces the bug before the fix
  - Avoid touching `settings.db` schema lightly — it survives user resets and schema changes need migration care

  **Code conventions:**
  - Settings at call time: `settings.get('group', 'key')` inside functions, never at module level
  - Route ordering: specific routes before parameterised (`/catalog/inspect` before `/catalog/{hash}`)
  - Pydantic v2: optional fields are `str | None = None`, not `str = None`
  - qdrant-client v1.17+: use `query_points()`, not `search()`

  **Known limitations / side quests:** See [docs/internals/SideQuests.md](docs/internals/SideQuests.md).

- [ ] **Step 4: Commit**

  ```bash
  git add CONTRIBUTING.md
  git commit -m "docs: add CONTRIBUTING.md with kernel guide and code conventions"
  ```

---

## Chunk 3: User Docs — Setup, Configuration, Troubleshooting

### Task 6: docs/setup.md

**Files:**
- Create: `docs/setup.md`

- [ ] **Step 1: Write prerequisites section**

  List everything that must be in place before running `setup.ps1`:

  **All platforms:**
  - Python 3.11+
  - Docker Desktop (for Qdrant)
  - Ollama (`ollama serve` running in background)
  - Git

  **Windows (native path):**
  - Tesseract OCR: download from https://github.com/UB-Mannheim/tesseract/wiki — install to `C:\Program Files\Tesseract-OCR\`
  - Poppler: download from https://github.com/oschwartz10612/poppler-windows — extract to `E:\DocVault\bin\poppler\` (or any path, configured in `config.ini`)

  **Linux/macOS (Docker path only):**
  - `wmi` is not installed and not needed — the WMI sensor is automatically replaced by a no-op stub
  - Tesseract and Poppler are not needed for the Docker path (DocVault itself runs natively, only Qdrant is in Docker)

- [ ] **Step 2: Write native Windows setup walkthrough**

  ```powershell
  git clone https://github.com/your-org/docvault.git
  cd docvault
  .\setup.ps1
  ```

  What `setup.ps1` does:
  1. Checks Python 3.11+
  2. Creates `venv/` and installs `requirements.txt`
  3. Asks for scan directory, Tesseract path, Poppler path, Ollama models, Qdrant host/port
  4. Writes `config.ini` with your answers
  5. Removes orphaned Docker containers from previous runs
  6. Starts the Qdrant container via `docker compose up -d qdrant`
  7. Pulls required Ollama models (`nomic-embed-text`, `minicpm-v`, `deepseek-r1:14b`)
  8. Checks Tesseract is reachable

  Then to start:
  ```powershell
  .\start.ps1
  ```

  Open `http://localhost:8000`.

- [ ] **Step 3: Write Docker setup section**

  ```bash
  git clone https://github.com/your-org/docvault.git
  cd docvault
  docker compose up
  ```

  Note: `docker compose up` starts **Qdrant only**. DocVault itself runs as a native Python process. This is intentional — it avoids packaging GPU drivers and large model weights inside Docker.

  For Linux/macOS users:
  1. Install Python 3.11+ natively
  2. Install Ollama natively: https://ollama.com
  3. Pull models: `ollama pull nomic-embed-text && ollama pull minicpm-v && ollama pull deepseek-r1:14b`
  4. Install Tesseract: `sudo apt install tesseract-ocr` (Ubuntu) or `brew install tesseract` (macOS)
  5. Install Poppler: `sudo apt install poppler-utils` or `brew install poppler`
  6. Create venv: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
  7. Copy `config.ini.example` to `config.ini` and edit paths (platform-appropriate)
  8. `python run.py`

  Note on `wmi`: The `wmi` package is listed in `requirements.txt` for Windows. On Linux/macOS, the import fails gracefully — CPU temperature sensing is replaced by a DummySensor and the system runs normally. You can safely ignore the import warning on non-Windows systems.

- [ ] **Step 4: Write Ollama model reference and verification**

  | Model | Used for | Size |
  |-------|----------|------|
  | `nomic-embed-text` | Semantic embeddings | ~275 MB |
  | `minicpm-v` | Image description, vision AI | ~5 GB |
  | `deepseek-r1:14b` | RAG chat, code analysis | ~9 GB |

  Verification: after `start.ps1` (or `python run.py`), open `http://localhost:8000` and navigate to Settings → System Health. Green indicators confirm Qdrant, Ollama, and the database are reachable.

- [ ] **Step 5: Commit**

  ```bash
  git add docs/setup.md
  git commit -m "docs: add docs/setup.md with native Windows and Docker paths"
  ```

---

### Task 7: docs/configuration.md

**Files:**
- Create: `docs/configuration.md`

- [ ] **Step 1: Write intro and settings resolution chain**

  DocVault resolves settings in priority order:
  1. **vault_settings** (per-vault overrides in `settings.db`) — highest priority
  2. **settings.db** (user-set values, survives resets)
  3. **config.ini** (deployment defaults set by `setup.ps1`)
  4. **Schema default** (built-in fallback) — lowest priority

  There are two ways to change settings:
  - Edit `config.ini` for deployment defaults (affects all vaults)
  - Use the Settings page in the web UI to write to `settings.db` (survives resets, overrides `config.ini`)

- [ ] **Step 2: Write the full settings reference table**

  Document every setting key, group, default, and description. Group by INI section:

  **[paths]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `scan_directory` | *(required)* | Root directory to scan. Set by `setup.ps1`. |
  | `cache_directory` | `<repo>/.cache/extracted_images` | Where extracted image thumbnails are stored. |

  **[database]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `sqlite_path` | `<repo>/docvault.db` | Path to the main operational database. |

  **[llm]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `provider` | `ollama` | LLM backend. Only `ollama` is currently supported. |

  **[tesseract]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `path` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Absolute path to the Tesseract binary. Linux/macOS: `/usr/bin/tesseract`. |

  **[ollama]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `host` | `http://localhost:11434` | Ollama server URL. |
  | `chat_model` | `deepseek-r1:14b` | Model used for RAG and chat. |
  | `embed_model` | `nomic-embed-text` | Model used for semantic embeddings. |
  | `num_ctx` | `8192` | Context window size for chat. |
  | `temperature` | `0.1` | Sampling temperature for chat. |
  | `num_predict` | `-1` | Max tokens to generate (-1 = model default). |
  | `top_p` | `0.9` | Top-p sampling parameter. |
  | `repeat_penalty` | `1.1` | Repetition penalty. |
  | `max_parallel` | `1` | Max concurrent Ollama requests. |

  **[qdrant]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `host` | `localhost` | Qdrant host. |
  | `port` | `6333` | Qdrant port. |

  **[pdf]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `poppler_path` | `<repo>\bin\poppler\Library\bin` | Path to the Poppler `bin` directory. Linux/macOS: leave empty if Poppler is on PATH. |
  | `sparse_threshold` | `50` | Char count below which a PDF page is considered image-only and sent to OCR. |

  **[vision]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `model` | `minicpm-v` | Ollama vision model for image description. |
  | `describe_images` | `true` | Enable AI image description. Set `false` to skip (faster, no vision AI). |

  **[embeddings]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `chunk_size` | `600` | Characters per embedding chunk. |
  | `chunk_overlap` | `100` | Overlap between consecutive chunks. |
  | `score_threshold` | `0.65` | Minimum semantic similarity score for results to appear. |

  **[search]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `rag_top_k` | `5` | Number of chunks retrieved for RAG context. |
  | `rag_threshold` | `0.50` | Minimum similarity for RAG chunks. |
  | `result_limit` | `20` | Max search results returned. |
  | `fts_weight` | `0.4` | FTS weight in hybrid search (must sum to 1.0 with `sem_weight`). |
  | `sem_weight` | `0.6` | Semantic weight in hybrid search. |

  **[video]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `describe_frames` | `true` | Enable AI frame description for videos. |
  | `frame_interval` | `10` | Sample one frame every N seconds for visual description. |

  **[monitor]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `search_throttle_duration` | `30` | Seconds to throttle workers after a user search. |
  | `stuck_task_threshold_mins` | `30` | Minutes before a PROCESSING task is considered stuck. |

  **[google]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `credentials_path` | `<repo>\credentials\google_credentials.json` | Path to Google API OAuth credentials (optional — only needed for Google Drive extraction). |
  | `token_path` | `<repo>\credentials\google_token.json` | Path to cached OAuth token. |

  **[alerts]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `ntfy_url` | *(empty)* | Optional ntfy.sh URL for push notifications on critical alerts. |

  **[system]**
  | Key | Default | Description |
  |-----|---------|-------------|
  | `debug_mode` | `false` | Enable verbose INFO/DEBUG logging to console. |

  Add a note: Settings marked Windows-only (`wmi`, GPU monitoring) fail silently on Linux/macOS — the system continues with the relevant sensor disabled.

- [ ] **Step 3: Write settings.db overrides section**

  Explain that the Settings page in the UI writes directly to `settings.db`. These values take priority over `config.ini` and survive `reset.ps1`. To clear a `settings.db` override, either set the value back in the Settings UI or delete the key from `settings.db` with SQLite.

- [ ] **Step 4: Commit**

  ```bash
  git add docs/configuration.md
  git commit -m "docs: add docs/configuration.md with full settings reference"
  ```

---

### Task 8: docs/troubleshooting.md

**Files:**
- Create: `docs/troubleshooting.md`

- [ ] **Step 1: Write common startup failures**

  **Port 8000 already in use**
  ```
  Error: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)
  ```
  Fix: Another process is on port 8000. Find it: `netstat -ano | findstr :8000` (Windows) or `lsof -i :8000` (Linux/macOS). Kill it, or change the bind port in settings.

  **Qdrant not connecting**
  ```
  ConnectionRefusedError: [Errno 111] Connection refused
  ```
  Fix:
  1. `docker ps` — is `docvault-qdrant-1` running?
  2. If not: `docker start docvault-qdrant-1`
  3. If the container doesn't exist: `docker compose up -d qdrant`
  4. Wait 10 seconds and retry. Check: `curl http://localhost:6333/collections`

  **Qdrant degraded mode banner in search UI**
  DocVault continues working without Qdrant — semantic search returns empty results and hybrid search falls back to FTS-only. The amber banner in the search UI confirms this. Fix Qdrant to restore full search.

  **Ollama not running**
  ```
  [!!] Ollama is not running. Embeddings and LLM features will fail.
  ```
  Fix: `ollama serve` in a separate terminal. DocVault will continue without it — extraction still works, embeddings and RAG will fail until Ollama is available.

  **venv not found**
  ```
  [XX] venv not found. Run setup.ps1 first.
  ```
  Fix: Run `.\setup.ps1`.

- [ ] **Step 2: Write extraction errors**

  **charmap OCR error (1,000+ tasks stuck in ERROR)**
  ```
  [ocr_extractor] OCR failed: 'charmap' codec can't encode character '\u2019'
  ```
  Cause: A Unicode character from OCR output was passed through a Windows-1252 encode somewhere. Fix: This is a known bug — see the Utilities page for a "Retry extraction errors" button once the underlying extractor is patched.

  **WMI access denied (non-admin Windows)**
  ```
  wmi.x_wmi: <x_wmi: Unexpected COM Error (-2147352567, ...)>
  ```
  Cause: The WMI sensor requires admin privileges on some Windows configurations. Fix: Run DocVault as administrator, or accept that CPU temperature will show 0°C (all other features work normally).

  **No Ollama slots available**
  ```
  Error: model is already loaded
  ```
  Cause: Ollama is processing another request. DocVault serialises Ollama calls by default (`ollama:max_parallel = 1`). If you see this frequently, check the Telemetry dashboard for GPU usage.

  **Database locked / OperationalError**
  ```
  sqlite3.OperationalError: database is locked
  ```
  Cause: Multiple processes accessing `docvault.db` simultaneously. Ensure only one DocVault instance is running. If it persists, use the Utilities → Health panel to check for stuck PROCESSING tasks.

- [ ] **Step 3: Write database and data issues**

  **Database corruption**
  If DocVault fails to start with an SQLite error, run:
  ```powershell
  .\reset.ps1
  ```
  This deletes `docvault.db` and Qdrant vectors. `settings.db` (your config and API keys) is never touched. Re-ingestion starts automatically when you run `start.ps1`.

  **Qdrant storage corruption**
  If Qdrant refuses to start after a crash, stop the container, delete `qdrant_storage/collections/docvault/`, and restart:
  ```powershell
  docker stop docvault-qdrant-1
  Remove-Item qdrant_storage\collections\docvault -Recurse -Force
  docker start docvault-qdrant-1
  ```
  Then use the Utilities page → Rebuild Index to re-embed all extracted content.

  **Files not appearing in search after ingestion**
  1. Check the Vault Log (catalog page) — are tasks EMBEDDED?
  2. If stuck at EXTRACTED: Qdrant may be down (see above)
  3. If stuck at PENDING: workers may not be running — check the index page for worker status

- [ ] **Step 4: Commit**

  ```bash
  git add docs/troubleshooting.md
  git commit -m "docs: add docs/troubleshooting.md with common failure patterns"
  ```

---

## Chunk 4: Architecture Doc

### Task 9: docs/architecture.md

**Files:**
- Create: `docs/architecture.md`

- [ ] **Step 1: Write system overview and component map**

  **System overview** (audience: developers):

  DocVault is a FastAPI application with three daemon worker threads. The frontend is server-rendered HTML with Tailwind CSS and vanilla JavaScript. There is no separate frontend build step — templates are in `frontend/`, served directly by FastAPI's `StaticFiles` mount.

  **Component map** — describe each file/module and its role:

  | Component | File(s) | Role |
  |-----------|---------|------|
  | Entry point | `run.py` | Starts FastAPI, spawns workers, starts HardwareMonitor |
  | API server | `api/main.py`, `api/routes/` | FastAPI app, all HTTP endpoints |
  | Extraction worker | `workers/extraction_worker.py` | Claims PENDING tasks, runs kernels, writes results |
  | Embedding worker | `workers/embedding_worker.py` | Claims EXTRACTED tasks, chunks text, upserts to Qdrant |
  | Art worker | `workers/art_worker.py` | Vision AI for artwork identification |
  | Resource governor | `core/monitor.py` | HardwareMonitor daemon, ThrottleStateMachine, sensor registry |
  | Extractor router | `core/router.py` | Maps file extensions to ordered lists of kernels |
  | Registry | `core/registry.py` | Discovers kernels, certifies, detects tampering |
  | Task queue | `core/manager.py` | SQLite layer: tasks, FTS5, vaults, settings, images, logs |
  | Settings | `core/settings.py` | 3-tier + 4-tier (vault-aware) settings resolution |
  | Vault manager | `core/vault_manager.py` | Vault CRUD and state machine |
  | Search (FTS) | `search/fts.py` | SQLite FTS5 full-text search |
  | Search (semantic) | `search/semantic.py` | Qdrant vector search |
  | Search (hybrid) | `search/hybrid.py` | Combines FTS + semantic with weighted scoring |
  | Extractor base | `core/extractors/base.py` | BaseExtractor, ExtractorContext, IngestResult |
  | Kernels | `extractors/*.py` | Individual file-type extractors |
  | Frontend | `frontend/*.html` | HTML pages (Vault Status, Search, Catalog, Lab, Telemetry, Identity) |

- [ ] **Step 2: Write data pipeline diagram (text art)**

  ```
  ┌──────────────────────────────────────────────────────────┐
  │                       File on disk                       │
  └──────────────────────────┬───────────────────────────────┘
                             │ Ingestor detects new/moved file
                             ▼
                    ┌────────────────┐
                    │  docvault.db   │  tasks table: PENDING
                    └───────┬────────┘
                            │
              ┌─────────────▼─────────────┐
              │     Extraction Worker      │
              │  picks kernel from Router  │
              │  calls kernel.run()        │
              └─────────────┬─────────────┘
                            │ writes text, metadata, images
                            ▼
                    ┌────────────────┐
                    │  docvault.db   │  tasks: EXTRACTED
                    │  FTS5 index    │  text indexed
                    └───────┬────────┘
                            │
              ┌─────────────▼─────────────┐
              │     Embedding Worker       │
              │  chunks text               │
              │  calls Ollama embed        │
              │  upserts to Qdrant         │
              └─────────────┬─────────────┘
                            │
                    ┌────────────────┐
                    │  docvault.db   │  tasks: EMBEDDED
                    │  Qdrant        │  vectors stored
                    └────────────────┘
  ```

- [ ] **Step 3: Write three-database layout and three-worker model sections**

  **Three databases:**

  | Database | File | Purpose | Survives reset? |
  |----------|------|---------|-----------------|
  | Operational | `docvault.db` | Tasks, FTS5 index, extracted text, images | No — deleted by `reset.ps1` |
  | Configuration | `settings.db` | User config, API keys, vault definitions, extractor registry | Yes — never touched |
  | Observability | `logs.db` | Task timings, worker errors, system stats | Optional (asked by reset.ps1) |

  **Why separate databases?**
  `settings.db` must survive a clean-slate reset. Users may reset docvault.db to force re-extraction after changing settings, but their API keys and vault configuration should never be at risk. Keeping them in separate files makes this guarantee trivial to implement and easy to communicate.

  **Priority scheduling:**
  The priority formula weights vault priority and extractor priority together, with an age bonus to prevent indefinite starvation:
  ```
  priority = ((10 - vault_priority) * extractor_priority) + (age_seconds / 3600)
  ```
  Higher values are processed first. Vault priority 1 (most important) produces the highest multiplier.

- [ ] **Step 4: Write settings resolution section**

  Settings are resolved in four tiers (highest priority first):
  1. `vault_settings` — per-vault overrides stored in `settings.db`
  2. `settings.db` — user-set values via the UI
  3. `config.ini` — deployment defaults from `setup.ps1`
  4. Schema default — built-in fallback in `core/settings.py`

  **Critical rule:** All configurable values must be read at call time, not at import time. Use `settings.get('group', 'key')` inside functions. Reading at import time breaks settings resolution and causes stale values.

- [ ] **Step 5: Commit**

  ```bash
  git add docs/architecture.md
  git commit -m "docs: add docs/architecture.md with component map and data pipeline"
  ```

---

## Chunk 5: Extractor Documentation

### Task 10: docs/extractors/contract.md

**Files:**
- Create: `docs/extractors/contract.md`

- [ ] **Step 1: Write MANIFEST specification**

  Every kernel must define a module-level `MANIFEST` dict:

  ```python
  MANIFEST = {
      "id":         "com.docvault.text.plain",   # reverse-domain, unique
      "version":    "1.0.0",                      # semver string
      "name":       "Plain Text Engine",           # human-readable
      "extensions": [".txt", ".md", ".rst"],       # lowercase, with dot
      "requires":   [],                            # pip package names (optional)
  }
  ```

  Required keys: `id`, `version`, `name`, `extensions`. `requires` is optional (defaults to `[]`).

  `target_type` can be `"file"` (default) or `"folder"` (for directory-level extractors like `music_collection_intelligence_extractor`).

  ID naming convention: `com.docvault.<domain>.<name>` for built-in kernels. Third-party kernels should use their own reverse domain: `com.example.myteam.extractor`.

- [ ] **Step 2: Write extract() signature and return contract**

  ```python
  from core.extractors.base import ExtractorContext, IngestResult

  def extract(file_path: str, ctx: ExtractorContext) -> tuple:
      ...
      return result, None          # success
      # or
      return None, "error message" # failure
  ```

  **Exact signature required.** The contract auditor verifies:
  - Function named `extract`
  - Parameter `file_path: str`
  - Parameter `ctx: ExtractorContext`

  The return tuple is `(IngestResult | None, str | None)`.

- [ ] **Step 3: Write ExtractorContext and IngestResult reference**

  **ExtractorContext** — passed to every `extract()` call:

  | Field | Type | Description |
  |-------|------|-------------|
  | `vault_id` | `str \| None` | Vault being processed (None for legacy tasks) |
  | `file_hash` | `str` | SHA-256 hash of the file — use as a stable identifier |
  | `cancel_token` | `threading.Event` | Check `.is_set()` periodically; abort if True |
  | `logger` | Logger | Pre-configured logger — use instead of print() |
  | `settings` | Settings | Settings resolver — call `.get(group, key)` at use time |
  | `timeout_secs` | `int` | Worker timeout — respect it in long operations |
  | `report_progress` | `Callable` | Optional progress callback `(current, total, message)` |

  **IngestResult** — the return envelope:

  | Field | Type | Description |
  |-------|------|-------------|
  | `text` | `str \| None` | Primary extracted text — will be FTS-indexed and embedded |
  | `metadata` | `dict` | Structured key-value metadata (author, date, dimensions, etc.) |
  | `images` | `list[dict]` | Extracted images: `{path, caption, page_number}` |
  | `child_tasks` | `list[dict]` | Files to dispatch as new tasks: `{file_path, source_hash}` |
  | `enrichments` | `dict` | Extra payload to merge into Qdrant vector payload |
  | `errors` | `list[ExtractorError]` | Non-fatal errors (logged but don't fail the task) |
  | `status` | `str` | `"ok"`, `"partial"`, or `"empty"` |
  | `extractor_name` | `str` | Set automatically by the framework |
  | `elapsed_secs` | `float` | Set automatically by the framework |

- [ ] **Step 4: Write certification lifecycle**

  Kernels go through a certification lifecycle:

  | Status | Meaning |
  |--------|---------|
  | `unverified` | Discovered but not yet certified |
  | `certified` | Passed contract audit; hash stored for tamper detection |
  | `tampered` | Hash changed since certification — automatically disabled |
  | `disabled` | Manually disabled via the Extractor Lab |

  Built-in kernels (IDs starting with `com.docvault.*`) are auto-certified on startup. Third-party kernels must be certified manually via the Extractor Lab UI or the `/utils/certify/register` API.

  Certification runs two checks:
  1. **Manifest check** — required keys present, types correct
  2. **Signature check** — `extract()` has the exact required signature

  A tampered kernel (file modified after certification) is disabled automatically and a critical alert is sent.

- [ ] **Step 5: Commit**

  ```bash
  git add docs/extractors/contract.md
  git commit -m "docs: add docs/extractors/contract.md — kernel contract reference"
  ```

---

### Task 11: docs/extractors/writing-an-extractor.md

**Files:**
- Create: `docs/extractors/writing-an-extractor.md`

- [ ] **Step 1: Write the worked example from scratch**

  Walk through building a complete, functional extractor. Use a realistic example: a `.toml` config file extractor.

  **Step-by-step guide structure:**
  1. Create the file `extractors/toml_extractor.py`
  2. Write the MANIFEST
  3. Write the `extract()` function
  4. Test with `test_kernel.py`
  5. Certify in the Extractor Lab
  6. Verify routing

  **Complete example to include in the doc:**

  ```python
  """TOML configuration file extractor."""
  try:
      import tomllib          # Python 3.11+
  except ImportError:
      import tomli as tomllib # pip install tomli for Python 3.10

  from core.extractors.base import ExtractorContext, IngestResult

  MANIFEST = {
      "id":         "com.example.config.toml",
      "version":    "1.0.0",
      "name":       "TOML Config Extractor",
      "extensions": [".toml"],
      "requires":   [],
  }


  def extract(file_path: str, ctx: ExtractorContext) -> tuple:
      try:
          with open(file_path, "rb") as f:
              data = tomllib.load(f)
      except Exception as e:
          return None, f"Failed to parse TOML: {e}"

      # Flatten the config into readable text
      lines = []
      def flatten(obj, prefix=""):
          if isinstance(obj, dict):
              for k, v in obj.items():
                  flatten(v, f"{prefix}{k}.")
          elif isinstance(obj, list):
              for i, v in enumerate(obj):
                  flatten(v, f"{prefix}{i}.")
          else:
              lines.append(f"{prefix.rstrip('.')}: {obj}")

      flatten(data)
      text = "\n".join(lines)

      result = IngestResult(
          text=text,
          metadata={"key_count": len(lines), "top_level_sections": list(data.keys())},
          status="ok" if text else "empty",
      )
      return result, None
  ```

  **Testing:**
  ```bash
  python tools/test_kernel.py toml_extractor path/to/pyproject.toml --pretty
  python tools/test_kernel.py toml_extractor --audit --pretty
  ```

  **Routing verification:** The extractor will be auto-discovered on next server start and routed to `.toml` files. No registration step required for file-type routing.

- [ ] **Step 2: Write "do / don't" section**

  **Do:**
  - Check `ctx.cancel_token.is_set()` before long loops
  - Use `ctx.logger` instead of `print()`
  - Read settings via `ctx.settings.get(group, key)` inside `extract()`
  - Return `(None, "descriptive error message")` on failure — don't raise
  - Set `status="partial"` when some content was extracted but errors occurred

  **Don't:**
  - Import heavy dependencies at module level (import inside `extract()` instead)
  - Read settings at module level
  - Write files to disk (use `IngestResult.images` for extracted images)
  - Modify `ctx` (it's shared state)
  - Swallow exceptions silently — report them via `IngestResult.errors` or the error return

- [ ] **Step 3: Write child task dispatch section**

  Explain `child_tasks`: when your extractor finds embedded files (e.g., a PDF with image attachments), it can dispatch them as new tasks rather than processing them inline:

  ```python
  child_tasks = [
      {"file_path": "/path/to/extracted/attachment.jpg", "source_hash": ctx.file_hash}
  ]
  result = IngestResult(text=main_text, child_tasks=child_tasks, ...)
  ```

  The embedding worker creates new task records for each child. They are routed through the normal extraction pipeline.

- [ ] **Step 4: Commit**

  ```bash
  git add docs/extractors/writing-an-extractor.md
  git commit -m "docs: add docs/extractors/writing-an-extractor.md with TOML worked example"
  ```

---

### Task 12: docs/extractors/catalogue.md

**Files:**
- Create: `docs/extractors/catalogue.md`

- [ ] **Step 1: Write intro and catalogue table**

  Opening: "DocVault ships 20 built-in kernels. All are enabled by default. Some require external binaries or model downloads — the Installation column calls these out."

  | Kernel | Name | File types | What it extracts | Installation |
  |--------|------|-----------|-----------------|--------------|
  | `plaintext_extractor` | Plain Text Engine | `.txt`, `.md`, `.rst`, `.log`, `.csv`, `.ini`, `.cfg`, `.yaml`, `.yml`, `.json`, `.xml`, `.html`, `.htm` | Full text, line count | Built-in |
  | `text_extractor` | PDF Text Engine | `.pdf` | Full text via pdfminer, page count, metadata. Sparse pages dispatched to OCR. | Built-in (pdfminer.six in requirements.txt) |
  | `microsoft_word_extractor` | Word Document Engine | `.docx` | Text, heading structure, metadata | Built-in (python-docx) |
  | `microsoft_excel_extractor` | Excel Engine | `.xlsx`, `.xls` | Cell text, sheet names, row/column counts | Built-in (openpyxl) |
  | `microsoft_powerpoint_extractor` | PowerPoint Engine | `.pptx` | Slide text, speaker notes, slide count | Built-in (python-pptx) |
  | `source_code_intelligence_extractor` | Source Code Intelligence | `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.h`, `.cs`, `.rb`, `.php`, `.sh`, `.sql`, `.swift`, `.kt`, `.r`, `.lua` | Full text, language, structure analysis via LLM | Requires Ollama chat model |
  | `image_extractor` | Image OCR Engine | `.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`, `.tiff`, `.webp` | OCR text, image dimensions, EXIF metadata | Requires Tesseract binary |
  | `intelligent_image_extractor` | Vision AI Engine | `.jpg`, `.jpeg`, `.png`, `.bmp`, `.gif`, `.tiff`, `.webp` | AI-generated visual description | Requires Ollama vision model |
  | `photo_intelligence_extractor` | Photo Intelligence | `.jpg`, `.jpeg`, `.png` | EXIF (GPS, camera, date), AI description, embedding-ready summary | Requires Ollama vision model |
  | `aural_intelligence_extractor` | Audio Transcription Engine | `.mp3`, `.wav`, `.flac`, `.ogg`, `.aac`, `.m4a`, `.opus` | Full speech transcription via Whisper | Requires Whisper (first-run model download, 1–2 GB) |
  | `aural_metadata_extractor` | Audio Metadata Engine | `.mp3`, `.flac`, `.ogg`, `.wav`, `.aac`, `.m4a`, `.wma`, `.opus` | ID3/Vorbis tags (title, artist, album, year, track, genre) | Built-in (mutagen) |
  | `music_collection_intelligence_extractor` | Music Collection Intelligence | Folder-level | Collection-wide analysis across an album or library folder | Requires Ollama chat model |
  | `face_analytics_extractor` | Face Analytics Engine | `.jpg`, `.jpeg`, `.png`, `.bmp` | Face detection, emotion analysis, age/gender estimation | Requires mediapipe, fer (TensorFlow) |
  | `face_identity_extractor` | Face Identity Engine | `.jpg`, `.jpeg`, `.png`, `.bmp` | Face embeddings for identity matching | Requires face_recognition (dlib) |
  | `face_narrative_intelligence_extractor` | Face Narrative Intelligence | `.jpg`, `.jpeg`, `.png`, `.bmp` | LLM-generated narrative from face analytics results | Requires face_analytics results + Ollama chat model |
  | `media_technical_diagnostics_extractor` | Media Technical Diagnostics | `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.mp3`, `.flac`, `.wav` | Codec, bitrate, resolution, duration, stream map | Requires ffprobe (ffmpeg) |
  | `multimodal_video_intelligence_extractor` | Video Intelligence Engine | `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm` | Frame-sampled visual descriptions + audio transcription | Requires Ollama vision + ffmpeg + Whisper |
  | `archive_xray_extractor` | Archive X-Ray Engine | `.zip`, `.tar`, `.gz`, `.bz2`, `.7z`, `.rar`, `.tar.gz`, `.tar.bz2` | Full file manifest, nested extensions, total size, structure summary | Built-in (zipfile, tarfile; 7z/rar require 7-Zip or unrar on PATH) |
  | `gdrive_extractor` | Google Drive Engine | `.gdoc`, `.gsheet`, `.gslides` | Exported document text via Google Drive API | Requires Google OAuth credentials (see `credentials/`) |
  | `fallback_kernel` | Fallback Kernel | All unrecognised types | Records the file as seen; extracts filename and extension only | Built-in |

- [ ] **Step 2: Write installation requirements section**

  Break out the external dependencies into a reference block:

  **Tesseract OCR**
  - Windows: https://github.com/UB-Mannheim/tesseract/wiki
  - Ubuntu: `sudo apt install tesseract-ocr`
  - macOS: `brew install tesseract`

  **Poppler** (PDF image extraction)
  - Windows: https://github.com/oschwartz10612/poppler-windows
  - Ubuntu: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`

  **ffmpeg / ffprobe** (video and audio)
  - Windows: https://ffmpeg.org/download.html — add to PATH
  - Ubuntu: `sudo apt install ffmpeg`
  - macOS: `brew install ffmpeg`

  **Whisper** (audio transcription)
  - Installed via requirements.txt (`openai-whisper`)
  - Models download automatically on first use to `~/.cache/whisper/`
  - Recommended model: `base` (140 MB) for CPU; `large-v3` for GPU

  **face_recognition / dlib** (face identity)
  - Requires CMake and a C++ compiler to build
  - Windows: install Visual Studio Build Tools first
  - `pip install face_recognition` (in the venv)

  **Google Drive**
  - Requires OAuth credentials from Google Cloud Console
  - See `credentials/README.md` (not committed — ask the maintainer)

- [ ] **Step 3: Commit**

  ```bash
  git add docs/extractors/catalogue.md
  git commit -m "docs: add docs/extractors/catalogue.md with all built-in kernels"
  ```

---

## Chunk 6: Tools Documentation

### Task 13: docs/tools/test-kernel.md

**Files:**
- Create: `docs/tools/test-kernel.md`
- Remove: `docs/test-kernel-cli.md` (content expanded into this file)

- [ ] **Step 1: Read existing docs/test-kernel-cli.md**

  The existing file at `docs/test-kernel-cli.md` is thorough. The new file should:
  - Copy all existing content
  - Add a "Common patterns" section at the end
  - Add a note about the `--vault` flag and settings resolution
  - Correct any paths that still say `tools/test_kernel.py` (verify this is still accurate)

- [ ] **Step 2: Write the new file**

  Copy all content from `docs/test-kernel-cli.md` verbatim into `docs/tools/test-kernel.md`.

  Add the following section at the end:

  ```markdown
  ## Common patterns

  **Audit all kernels at once:**
  ```bash
  for kernel in extractors/*_extractor.py; do
      python tools/test_kernel.py "$kernel" --audit --quiet
  done
  ```

  **Compare output before and after changes:**
  ```bash
  python tools/test_kernel.py my_extractor sample.pdf --output before.json
  # (make changes)
  python tools/test_kernel.py my_extractor sample.pdf --output after.json
  # diff them
  python -c "
  import json
  a = json.load(open('before.json'))
  b = json.load(open('after.json'))
  print('text_chars:', a['text_chars'], '->', b['text_chars'])
  print('metadata keys:', sorted(a['metadata']), '->', sorted(b['metadata']))
  "
  ```

  **Vault-aware test (respects vault-level settings overrides):**
  ```bash
  python tools/test_kernel.py my_extractor sample.pdf --vault vault_abc123 --pretty
  ```
  ```

- [ ] **Step 3: Remove the old file**

  ```bash
  git rm docs/test-kernel-cli.md
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add docs/tools/test-kernel.md
  git commit -m "docs: expand test-kernel CLI doc into docs/tools/test-kernel.md"
  ```

---

### Task 14: docs/tools/build-art-index.md

**Files:**
- Create: `docs/tools/build-art-index.md`

> **Advanced / Optional** — only relevant if you have an art collection configured.

- [ ] **Step 1: Write the build-art-index doc**

  **Header note:**
  > This document is for users who have a collection of paintings, prints, or other artworks and want DocVault to identify them by name and artist when it encounters them in photos or scanned catalogues.

  **What the art index is:**
  A separate Qdrant collection (`art_index`) containing visual embeddings of known artworks. When the art enrichment worker finds an image, it compares the image against this index. A match above the similarity threshold adds artwork metadata (title, artist, year, source) to the document's Qdrant payload, making art photos searchable by artwork name.

  The art index is **separate from the main docvault collection** and is never touched by `reset.ps1`.

  **What `tools/build_art_index.py` does:**
  - Reads a directory of reference artwork images (one image = one artwork)
  - Generates visual embeddings via Ollama vision
  - Upserts to the `art_index` Qdrant collection with metadata from sidecar `.nfo` files

  **Usage:**
  ```bash
  python tools/build_art_index.py --source E:\art_references\ --pretty
  ```

  **Sidecar `.nfo` format:**
  Alongside each image (`portrait.jpg`), place a `portrait.nfo`:
  ```ini
  [artwork]
  title = Portrait of a Woman
  artist = Johannes Vermeer
  year = 1665
  source = Rijksmuseum
  ```

  **Checking the index:**
  ```bash
  # How many artworks are indexed?
  curl http://localhost:6333/collections/art_index
  ```

  **Extending the index:**
  Run `build_art_index.py` again with new images — it upserts incrementally (existing IDs are updated, not duplicated). The hash of the image file is used as the Qdrant point ID.

  **Similarity threshold:**
  Art identification uses `embeddings:score_threshold` from settings. Lower the threshold to match more loosely; raise it to reduce false positives.

- [ ] **Step 2: Commit**

  ```bash
  git add docs/tools/build-art-index.md
  git commit -m "docs: add docs/tools/build-art-index.md — art index seeding guide"
  ```

---

## Final Step: Verify and link

- [ ] **Step 1: Spot-check all internal links in README.md**

  Verify that every `[link text](path)` in `README.md` resolves to a file that now exists. Key links to check:
  - `docs/architecture.md` ✓
  - `docs/extractors/contract.md` ✓
  - `CONTRIBUTING.md` ✓
  - `LICENSE` ✓

- [ ] **Step 2: Spot-check that CLAUDE.md references are updated**

  Confirm `CLAUDE.md` references to `docs/plans/` and `docs/status/` have been updated to `docs/internals/plans/` and `docs/internals/status/`.

- [ ] **Step 3: Final commit**

  ```bash
  git add -A
  git commit -m "docs: final link verification pass"
  ```

- [ ] **Step 4: Push**

  ```bash
  git push
  ```
