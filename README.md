# DocVault

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](docs/setup.md)

A local-first document intelligence platform — scan your files, extract every meaningful signal, search everything.

---

[Screenshot: Search results — coming soon]

---

## Philosophy & Design

DocVault exists because most "search my files" tools stop at the text layer. A PDF is more than the words `pdfminer` can pull out of it — it's also the scanned page images inside it, which have their own text and content. A photo is more than pixels — it's GPS coordinates, camera settings, faces, and a scene an AI can describe in words. An audio file is a transcript waiting to happen. DocVault's guiding principle is **maximum extraction**: every file should yield as much structured, searchable signal as it possibly can, using whatever combination of parsing, OCR, vision AI, and transcription applies. Where most systems pick the single "best" extractor per file type, DocVault deliberately runs every kernel that matches — a `.jpg` is processed by an OCR pass, an EXIF/GPS pass, a scene-description pass, and a face-detection pass, each contributing a different layer to the same document record (see [the overlapping-kernels table](docs/extractors/catalogue.md#4-notes-on-overlapping-kernels)).

**Local-first is a hard constraint, not a checkbox.** Every piece of AI inference — embeddings, vision descriptions, RAG chat — runs against a local Ollama instance. The vector store (LanceDB) is an embedded library with no server process to run or secure. Nothing leaves the machine unless a user deliberately configures an external API key for an optional feature (e.g. cloud fallback for art identification). This constraint is why LanceDB replaced an earlier Qdrant-based design for the main document store: an external database service is one more thing to install, secure, and keep running, and "local-first" should mean the software actually behaves that way by default, not just that it's *capable* of running offline.

**The system is architected like a small, self-governing operating system**, and thinking of it that way is the fastest way to understand the codebase:

| OS concept | DocVault component | Role |
|---|---|---|
| Kernel / registry | `core/manager.py` | SQLite-backed task queue and content store — tracks every document and its processing state |
| Mount manager | `core/vault_manager.py` | Vaults are mounted namespaces, each with its own priority, settings, and lifecycle |
| Scheduler | `workers/*.py` | Three daemon workers claim tasks by composite priority, process them, and hand off to the next stage |
| Interrupt vector table | `core/router.py` | Maps file extensions to the ordered list of kernels that should run against them |
| Drivers | `extractors/*.py` | Each kernel implements one fixed contract — `extract(file_path, ctx) -> (IngestResult, error)` — so the scheduler never needs to know *how* a file was processed, only what came back |
| Power/thermal management | `core/monitor.py` | The resource governor samples CPU/GPU/disk and throttles workers under load, the same way an OS avoids cooking the hardware |
| Syscall interface | `api/` (FastAPI) | The only way the frontend touches the system's internals |
| Shell | `frontend/*.html` | The user-facing view onto vault state, search, and telemetry |

**Nothing gets to starve.** The task queue orders work by `((10 - vault_priority) * extractor_priority) + (age_seconds / 3600)`. A vault marked low-priority still gets processed — the age term grows every hour a task waits, so eventually it outranks anything newer. This is the same aging technique OS schedulers use to prevent priority inversion from starving background work forever.

**Degrade, don't crash.** Nearly every subsystem has a documented fallback path rather than a failure mode: semantic search that times out falls back to FTS-only with a visible "degraded" banner instead of erroring; a CPU temperature sensor that WMI can't reach is swapped for a dummy 0°C reading instead of taking down the resource governor; a worker that can't reach Ollama keeps extracting text and indexing FTS while embeddings queue up for later. The rule of thumb throughout the codebase: a missing piece should shrink the feature set, never take down the whole system.

**Three databases, three lifecycles.** `docvault.db` (tasks, extracted text, FTS5 index) is treated as disposable — safe to delete and let re-extraction rebuild it. `settings.db` (vault definitions, user configuration, API keys) must survive that reset, so it lives in its own file and `reset.ps1` never touches it. `logs.db` (timings, errors, sensor history) is pure observability and can be cleared without losing anything that matters to the running system. Splitting these into separate files makes the "which data is safe to nuke" question trivial to answer and enforce, rather than a convention someone has to remember.

**Settings are resolved, not hardcoded**, through a four-tier chain — per-vault override → user setting in `settings.db` → `config.ini` deployment default → built-in schema default — and the one rule enforced everywhere in the codebase is that values are read *at call time*, never cached at import time. This exists because DocVault's workers are long-running daemon threads: a setting changed through the UI mid-run must take effect on the worker's next loop, not require a restart.

**Simplicity over resilience where it's cheap to do so.** DocVault runs as a single native process with daemon worker threads and no graceful shutdown drain — `run.py` forces `os._exit(0)` shortly after a stop signal rather than waiting for in-flight work to finish cleanly. This trade only works because tasks are resumable by design: a task killed mid-extraction is picked back up from its last committed state on the next run, so there's nothing to lose by exiting hard. One command starts everything; Ctrl+C stops it.

---

## Features

### Extraction
- Text extraction from PDF, Word, Excel, PowerPoint, plaintext, and source code
- OCR via Tesseract for scanned documents and embedded images
- Vision AI descriptions via Ollama (image content, scene understanding)
- Audio transcription via Whisper (speech-to-text for audio and video)
- EXIF and media metadata (camera settings, GPS, codec details, duration)
- Face detection, recognition, and emotion analytics
- Archive cataloguing (ZIP, TAR, 7z, RAR — file manifest and nested type analysis)

### Search
- Full-text search (SQLite FTS5) with wildcard (`*`, `?`) and regex (`/pattern/`) modes
- Semantic search via an embedded LanceDB vector store (no external service or Docker required)
- Hybrid FTS + semantic scoring with graceful degradation to FTS-only if the semantic step is slow or unavailable
- RAG — ask questions in natural language, get cited, span-grounded answers from your own documents (streaming responses)
- Filename search with the same wildcard/regex auto-detection

### Management
- Multi-vault support — organise collections with independent priorities and settings
- Extractor Lab UI — browse, certify, test, and simulate extraction kernels
- Real-time resource governor — CPU, GPU, and disk monitoring with automatic worker throttling
- Hardware telemetry dashboard — live sensor readings, throttle state, and historical stats
- Identity Hub — face search across your photo collection
- Deleted-file detection — a removed file is soft-flagged and hidden from search rather than left as a dead search result; extracted text, images, and embeddings are preserved and auto-restored if the file reappears; permanent purge is a manual, explicit action

### Local-First
- All processing runs on your machine by default. No cloud calls unless you explicitly configure them.
- Ollama provides the LLM and vision layer (self-hosted); LanceDB provides vector search as an embedded library — no separate database service to run.
- RAG chat can optionally use the Anthropic API instead of Ollama (`llm:provider = claude` in Settings) — this is the one supported case where a request leaves the machine, and it only happens if you configure it.

---

## Hardware Requirements

|  | Minimum | Recommended |
|--|---------|-------------|
| **CPU** | Any modern x86-64 | Ryzen 9 / i9 class |
| **RAM** | 8 GB | 32 GB+ |
| **GPU** | None (CPU-only Ollama) | NVIDIA 8 GB+ VRAM |
| **Storage** | 20 GB free | SSD, 100 GB+ |
| **OS** | Windows 10/11, Linux, macOS | Windows 11 (reference) |

> **Honest expectations:** DocVault was built and tuned on a Ryzen 9 7950X3D / 128 GB RAM / RTX 4090. On the minimum tier, text extraction, FTS, and basic RAG work well. Vision AI (image description, face detection) and audio transcription (Whisper) run on CPU but are slow — minutes per file, not seconds. A GPU is the single biggest performance upgrade.

> **NVIDIA GPU:** `nvidia-ml-py` is used for GPU temperature and utilisation monitoring. On non-NVIDIA hardware, the sensor fails silently and the system continues normally.

> **Windows WMI:** CPU temperature sensing uses the `wmi` package (Windows only). On Linux/macOS — including inside Docker — the WMI sensor is replaced by a no-op stub automatically.

---

## Quickstart

DocVault runs as a single native Python process — no Docker, no external database service. Vector search (LanceDB) is an embedded library that lives alongside the app.

### Windows

```powershell
git clone https://github.com/your-org/docvault.git
cd docvault
.\setup.ps1      # one-time: venv, config, Ollama model pulls
.\start.ps1      # every time: checks Ollama, starts app with auto-restart
```

Open http://localhost:8050

### Linux / macOS

```bash
git clone https://github.com/your-org/docvault.git
cd docvault
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open http://localhost:8050 — see [docs/setup.md](docs/setup.md) for the full prerequisites and walkthrough (Tesseract, Poppler, Ollama models).

---

## What Gets Extracted

| File type | Extensions | What DocVault extracts |
|-----------|-----------|------------------------|
| PDF | `.pdf` | Full text, embedded images (dispatched to OCR/vision), metadata |
| Word | `.docx` | Text, paragraph structure, metadata |
| Excel | `.xlsx`, `.xls` | Cell text, sheet names, metadata |
| PowerPoint | `.pptx` | Slide text, speaker notes, metadata |
| Plaintext | `.txt`, `.md`, `.rst`, `.log`, `.csv`, `.yaml`, `.json`, `.xml` | Full text |
| Source code | `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.cs`, `.rb`, `.sql` and more | Code text, language, structure analysis |
| Images | `.jpg`, `.png`, `.bmp`, `.gif`, `.tiff`, `.webp` | OCR text, AI visual description, EXIF metadata, face detection |
| Audio | `.mp3`, `.flac`, `.wav`, `.ogg`, `.aac`, `.m4a`, `.opus` | Whisper transcription, metadata tags |
| Video | `.mp4`, `.mkv`, `.avi`, `.mov`, `.webm` | Frame-sampled visual descriptions, audio transcription, technical metadata |
| Archives | `.zip`, `.tar`, `.gz`, `.7z`, `.rar` | File manifest, nested types, total size |

---

## Architecture

Three daemon workers share a SQLite task queue: the extraction worker reads files and runs extraction kernels, the embedding worker chunks extracted text and upserts vectors to LanceDB, and the art enrichment worker identifies artworks and enriches metadata. A resource governor runs as a background daemon, monitoring CPU, GPU, and disk pressure and throttling workers automatically when the system is under load. See [Philosophy & Design](#philosophy--design) above for why the system is shaped this way, and [docs/architecture.md](docs/architecture.md) for the full component map and data-flow diagram.

---

## Writing Extractors

DocVault's extraction system is built on a kernel contract: a Python file with a `MANIFEST` dict and a typed `extract()` function. Any file matching that contract can be dropped into `extractors/` and will be discovered, certified, and routed automatically. See [docs/extractors/contract.md](docs/extractors/contract.md) to get started.

---

## Management Scripts

```
.\setup.ps1   One-time setup: venv, config.ini, Ollama model pulls
.\start.ps1   Start the system: checks/launches Ollama, then launches DocVault (with auto-restart)
.\reset.ps1   Clean slate: deletes docvault.db and LanceDB vectors; preserves settings.db
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
