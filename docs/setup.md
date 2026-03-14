# DocVault — First-Time Setup Guide

DocVault is a local-first document intelligence platform: FastAPI backend, SQLite (3 databases), Qdrant vector store, and Ollama LLM runtime. The reference platform is Windows 11 native. Linux and macOS users follow the Docker path below.

---

## 1. Prerequisites

### All platforms

- Python 3.11+
- Docker Desktop — used to run the Qdrant vector database
- Ollama — LLM runtime; `ollama serve` must be running before you start DocVault
- Git

### Windows (native path only)

- **Tesseract OCR** — install from https://github.com/UB-Mannheim/tesseract/wiki
  Default install path: `C:\Program Files\Tesseract-OCR\`
- **Poppler** (PDF image extraction) — download from https://github.com/oschwartz10612/poppler-windows and extract to any directory; the path is configured in `config.ini`

### Linux / macOS

The native setup script (`setup.ps1`) is Windows-only. Linux and macOS users should follow the Docker path in section 3.

---

## 2. Native Windows Setup

```powershell
git clone https://github.com/your-org/docvault.git
cd docvault
.\setup.ps1
```

`setup.ps1` performs the following steps automatically:

1. Verifies Python 3.11+ is available on `PATH`
2. Creates a `venv/` virtual environment and installs `requirements.txt`
3. Prompts for your configuration — press Enter to accept the default for any field:
   - Scan directory (the folder DocVault will index)
   - Tesseract install path
   - Poppler bin path
   - Ollama host and model names
   - Qdrant host and port
4. Writes `config.ini` with your answers
5. Removes any orphaned Docker containers from previous runs
6. Starts the Qdrant container: `docker compose up -d qdrant`
7. Pulls the required Ollama models: `nomic-embed-text`, `minicpm-v`, `deepseek-r1:14b`
8. Confirms Tesseract is reachable at the configured path

### Starting DocVault

After setup, start every session with:

```powershell
.\start.ps1
```

Then open http://localhost:8000.

---

## 3. Docker Path (Linux / macOS / Windows)

`docker compose up` starts **Qdrant only**. DocVault itself always runs as a native Python process — this avoids packaging GPU drivers and large AI model weights inside a container.

### Ubuntu / Debian

```bash
git clone https://github.com/your-org/docvault.git
cd docvault

# Start Qdrant
docker compose up -d

# Install system dependencies
sudo apt install python3.11 python3.11-venv tesseract-ocr poppler-utils ffmpeg

# Install Ollama and pull models
curl -fsSL https://ollama.com/install.sh | sh
ollama pull nomic-embed-text
ollama pull minicpm-v
ollama pull deepseek-r1:14b

# Set up Python environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure paths
# If a config.ini.example exists: cp config.ini.example config.ini
# Otherwise copy config.ini from a Windows setup, then edit:
#   scan_directory = /path/to/your/documents
#   tesseract_path = /usr/bin/tesseract
#   poppler_path   = (leave empty — poppler-utils installs to PATH)

# Start DocVault
python run.py
```

### macOS

```bash
brew install tesseract poppler ffmpeg
```

Then follow the same Python, Ollama, and venv steps as above, substituting:

- `tesseract_path = /usr/local/bin/tesseract` (Intel) or `/opt/homebrew/bin/tesseract` (Apple Silicon)
- `poppler_path = ` (empty — Homebrew puts pdftoppm on PATH)

### `wmi` package note

On Linux and macOS the `wmi` package (used for CPU temperature monitoring on Windows) is not installed. DocVault detects this automatically and replaces the WMI sensor with a no-op stub — all features work normally. A warning may appear in the logs on startup; this is expected and harmless.

---

## 4. Ollama Model Reference

| Model | Used for | Approximate size |
|---|---|---|
| `nomic-embed-text` | Semantic embeddings | ~275 MB |
| `minicpm-v` | Image description, vision AI | ~5 GB |
| `deepseek-r1:14b` | RAG chat, code analysis | ~9 GB |

If Ollama is not running when DocVault starts, embedding and RAG features will fail until it comes online. Text extraction and FTS (full-text) search continue to work without Ollama.

---

## 5. Verification

After `.\start.ps1` (or `python run.py`), open http://localhost:8000.

- **Index page** — shows vault status and worker health; green indicators confirm workers are running.
- **Settings → System Health** — confirms Qdrant, Ollama, and the databases are reachable.
- **Search tab** — run a simple filename search to confirm the system is live end-to-end.

If anything is wrong, see [docs/troubleshooting.md](troubleshooting.md).
