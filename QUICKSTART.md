# DocVault Quick-Start Guide

Get DocVault running and searching your first documents in a few minutes. This is the fast path — for VRAM tuning, autostart, the Windows Ollama-port gotcha, and the full hardware-tier breakdown, see [docs/setup.md](docs/setup.md). If something goes wrong, [docs/troubleshooting.md](docs/troubleshooting.md) covers the common failure modes.

---

## 1. What you need first

- **Python 3.11+**
- **Git**
- **[Ollama](https://ollama.com)** — installed, and `ollama serve` reachable
- **Tesseract OCR** — [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki) · `apt install tesseract-ocr` · `brew install tesseract`
- **Poppler** (PDF image extraction) — [Windows build](https://github.com/oschwartz10612/poppler-windows) · `apt install poppler-utils` · `brew install poppler`
- **ffmpeg** (audio/video) — [Windows build](https://www.gyan.dev/ffmpeg/builds/) · `apt install ffmpeg` · `brew install ffmpeg`

A GPU is strongly recommended but not required — see the [Hardware Requirements](README.md#hardware-requirements) table if you're on modest hardware or want to skip local AI compute entirely.

## 2. Install

**Windows:**
```powershell
git clone https://github.com/AlbertLotito/DocVault.git
cd docvault
.\setup.ps1
```
`setup.ps1` creates the venv, installs dependencies, prompts for your scan directory and tool paths, writes `config.ini`, and pulls the three required Ollama models (~15 GB total: `nomic-embed-text`, `minicpm-v`, `qwen2.5:14b`).

**Linux/macOS:**
```bash
git clone https://github.com/AlbertLotito/DocVault.git
cd docvault
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
ollama pull nomic-embed-text && ollama pull minicpm-v && ollama pull qwen2.5:14b
```
Then copy `config.ini` from a Windows setup or write one by hand — see [docs/setup.md §3](docs/setup.md#3-manual-setup-linux--macos--windows-without-setupps1) for the exact keys (`scan_directory`, `tesseract_path`, `poppler_path`).

## 3. Run it

**Windows:** `.\start.ps1` (checks Ollama, launches DocVault, auto-restarts on crash)
**Linux/macOS:** `python run.py`

Open **http://localhost:8050**.

## 4. Confirm it's working

1. **Vault Status page** (`/vault`) — green indicators mean all three workers are running.
2. Wait for a file or two to finish processing (or check the **Browse page** at `/browse` — expand a folder, a file should show `COMPLETED` once done).
3. **Search page** — type a word you know is in one of your documents. A result confirms extraction, indexing, and search are all working end-to-end.

That's it — DocVault scans in the background continuously from here. First-time processing of a large collection takes real time (see [docs/setup.md](docs/setup.md) for what to expect on your hardware); you don't need to wait for it to finish before using search on what's already been processed.

## Next steps

- Add more vaults, tune extraction settings, or check hardware telemetry — all from the web UI.
- Writing your own extractor kernel? Start at [docs/extractors/writing-an-extractor.md](docs/extractors/writing-an-extractor.md).
- Full configuration reference (every setting, every default): [docs/configuration.md](docs/configuration.md).
