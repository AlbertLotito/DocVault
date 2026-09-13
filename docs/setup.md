# DocVault — High-End Workstation Install Guide

> In a hurry? [QUICKSTART.md](../QUICKSTART.md) at the repo root is the condensed version of this guide — clone, install, run, verify, nothing else. Come back here for the detail: VRAM tuning, the Ollama-port gotcha, autostart, and hardware-tier tradeoffs.

This guide documents the **reference install path**: a single-GPU Windows workstation with enough VRAM to run every AI-dependent feature locally at full speed. It is the configuration DocVault is built and tuned against (Ryzen 9 7950X3D / 128 GB RAM / RTX 4090), and the path that gets you the full feature set — vision descriptions, face analytics, audio/video transcription, and RAG chat — with no cloud calls at all.

**This is not the only supported tier.** DocVault also runs on modest hardware and CPU-only setups — see the [Hardware Requirements](../README.md#hardware-requirements) table in the README for the minimum tier, and [docs/internals/SideQuests.md §4](internals/SideQuests.md#4-no-gpu--subscription-ai-support) if you specifically want to avoid a local GPU entirely and lean on subscription AI APIs instead — that path has real gaps today and is documented honestly as a roadmap, not a guide you can follow yet.

LanceDB runs in-process — there is **no Docker requirement** and no external vector database service to install or run. Everything except Ollama runs as a single native Python process.

---

## 1. Prerequisites

### All platforms

- Python 3.11+
- Ollama — LLM runtime; `ollama serve` must be running before you start DocVault
- Git
- **ffmpeg** — required for audio/video extraction (Whisper transcription, frame-sampled video descriptions, and format probing for `.mp3`/`.opus`/`.m4b`/`.mp4`/`.wmv`/etc.). `ffmpeg-python` (in `requirements.txt`) is a thin binding — it calls out to a real `ffmpeg` executable that must be installed and reachable on `PATH` separately.

### Windows (native path only)

- **Tesseract OCR** — install from https://github.com/UB-Mannheim/tesseract/wiki
  Default install path: `C:\Program Files\Tesseract-OCR\`
- **Poppler** (PDF image extraction) — download from https://github.com/oschwartz10612/poppler-windows and extract to any directory; the path is configured in `config.ini`
- **ffmpeg** — download a build from https://www.gyan.dev/ffmpeg/builds/ (the "essentials" build is enough), extract it, and add its `bin/` folder to `PATH`. Confirm with `ffmpeg -version` in a new terminal.

### Linux / macOS

The setup script (`setup.ps1`) is Windows-only (PowerShell). Linux and macOS users should follow the manual setup in section 3 — the resulting DocVault process is identical.

---

## 2. Native Windows Setup

```powershell
git clone https://github.com/AlbertLotito/DocVault.git
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
4. Writes `config.ini` with your answers
5. Pulls the required Ollama models: `nomic-embed-text`, `minicpm-v`, `qwen2.5:14b`
6. Confirms Tesseract is reachable at the configured path

### Starting DocVault

After setup, start every session with:

```powershell
.\start.ps1
```

`start.ps1` checks that Ollama is running (auto-launching `ollama serve` and pulling any missing models if not), then launches DocVault with an exponential-backoff restart loop in case of crashes.

Then open http://localhost:8050.

### A Windows-specific gotcha: Ollama's port

Ollama's default port, `11434`, falls inside Windows' Hyper-V/WSL2 dynamic port exclusion range on some machines — anything with Hyper-V, WSL2, or Docker Desktop enabled (common on high-end workstations used for both gaming and dev work) can silently reserve a wide block of ports at boot, and `bind()` on an excluded port fails outright. If this happens to you, `ollama serve` won't be reachable on the default port and nothing obviously tells you why.

`start.ps1` handles this automatically: it detects an excluded/in-use port, picks a free one, and persists the choice to `config.ini`'s `[ollama] host` and to the `OLLAMA_HOST` environment variable, so it's stable across restarts. You don't need to do anything — but if you're ever debugging "why can't DocVault reach Ollama," check `[ollama] host` in `config.ini` before assuming Ollama itself is broken.

### Optional: run at Windows logon (autostart + tray icon)

```powershell
.\register_autostart.ps1
```

Registers two Windows Scheduled Tasks — the DocVault server and a system tray icon (`tray/tray_app.py`) — to launch automatically at logon. Safe to re-run any time, including after moving the project to a new drive or path; it updates the existing tasks in place rather than duplicating them. The tray icon gives you a quick "Open DocVault" / "Search…" popup / "Exit" menu without needing a terminal window open.

---

## 3. Manual Setup (Linux / macOS / Windows without `setup.ps1`)

DocVault always runs as a single native Python process — there's no container to build or run.

### Ubuntu / Debian

```bash
git clone https://github.com/AlbertLotito/DocVault.git
cd docvault

# Install system dependencies
sudo apt install python3.11 python3.11-venv tesseract-ocr poppler-utils ffmpeg

# Install Ollama and pull models
curl -fsSL https://ollama.com/install.sh | sh
ollama pull nomic-embed-text
ollama pull minicpm-v
ollama pull qwen2.5:14b

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

| Model | Used for | Disk size | Approx. VRAM when loaded |
|---|---|---|---|
| `nomic-embed-text` | Semantic embeddings | ~275 MB | ~1 GB |
| `minicpm-v` | Image/video description, vision AI, OCR fallback | ~5.5 GB | ~19 GB |
| `qwen2.5:14b` | RAG chat | ~9 GB | ~10 GB |

If Ollama is not running when DocVault starts, embedding and RAG features will fail until it comes online. Text extraction and FTS (full-text) search continue to work without Ollama.

### VRAM contention on a single GPU

`minicpm-v` (~19 GB loaded) and `qwen2.5:14b` (~10 GB loaded) don't both fit in VRAM at the same time on anything under ~28 GB of VRAM — including a 24 GB card like the RTX 4090 this guide is written against. Ollama unloads and reloads models on demand, which is fine functionally, but a naive default (Ollama's 5-minute idle unload) causes visible stalls: a vision-heavy extraction burst evicts the chat model, and the next RAG query has to wait for a multi-second reload, or vice versa.

DocVault sets a short `keep_alive` (60s) on the vision model specifically, so it releases VRAM promptly after an extraction burst instead of squatting on it for the default 5 minutes and starving the chat model. If you're running a smaller card, or a lot of concurrent vision + chat traffic, watch VRAM with `nvidia-smi` during heavy use — this is the first place to look if you see unexpected slowdowns rather than a hardware bottleneck.

---

## 5. Verification

After `.\start.ps1` (or `python run.py`), open http://localhost:8050.

- **Vault Status page** (`/vault`) — shows vault status and worker health; green indicators confirm workers are running.
- **Browse page** (`/browse`) — once your vault has scanned some files, expand a few folders in the tree and hover a file to confirm status/metadata tooltips are populating correctly. A good end-to-end smoke test: right-click a `PENDING` or `ERROR` file and use "Scan Now" — its status should flip to `PROCESSING` within a few seconds.
- **Settings → System Health** — confirms Ollama and the databases are reachable. LanceDB is embedded — there's no separate service to check.
- **Search page** — run a filename search, then a semantic search, to confirm both FTS and the vector store are live end-to-end.

If anything is wrong, see [docs/troubleshooting.md](troubleshooting.md).

---

## What this tier gets you

Every extraction kernel runs locally at full speed: vision descriptions and OCR for images, Whisper transcription for audio/video, face detection/analytics, and RAG chat — all without a single request leaving the machine. If you're on this tier, there's nothing further to configure for AI features; everything in the [Features](../README.md#features) list works out of the box once Ollama's models are pulled.
