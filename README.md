# DocVault

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](docs/setup.md)

A local-first document intelligence platform — scan your files, extract every meaningful signal, search everything.

---

[Screenshot: Search results — coming soon]

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

### Local-First
- All processing runs on your machine. No cloud calls unless you explicitly configure them.
- Ollama provides the LLM and vision layer (self-hosted); LanceDB provides vector search as an embedded library — no separate database service to run.

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

Three daemon workers share a SQLite task queue: the extraction worker reads files and runs extraction kernels, the embedding worker chunks extracted text and upserts vectors to LanceDB, and the art enrichment worker identifies artworks and enriches metadata. A resource governor runs as a background daemon, monitoring CPU, GPU, and disk pressure and throttling workers automatically when the system is under load.

Three SQLite databases keep concerns separated: `docvault.db` holds the task queue and extracted content (safe to delete and rebuild), `settings.db` holds user configuration, vault definitions, and API keys (survives data resets), and `logs.db` records per-task timings, worker errors, extractor statistics, and system sensor samples. LanceDB — an embedded vector database, no external service required — stores the semantic embedding vectors in `lancedb_storage/`.

See [docs/architecture.md](docs/architecture.md) for the full component map and data-flow diagram.

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
