# Project Status & Design Notes — 2026-03-05

This document is the authoritative reference for the current state of the DocVault project.

## 1. Major Milestone: Certified Plug-and-Play Architecture
DocVault has transitioned from a hard-coded kernel stack to a dynamic, database-driven **Kernel Registry**. This allows for the integration of new extraction intelligence (Python or external binaries) without modifying core system code.

### 1.1 Registry & Certification System
- **`ext_registry`**: A new table in `settings.db` tracks every installed kernel, its version, and a SHA-256 integrity hash.
- **Contract Enforcement**: Mandatory `MANIFEST` and **Strict Typing** (Python Type Hints) are now required for all kernels to pass certification.
- **Certification Scaffolding**: An interactive Lab utility that performs static audits and behavioral sandbox testing before allowing a kernel to be "signed" and activated.
- **Tamper Protection**: The system performs a pulse-check on boot. If a certified kernel file is modified on disk, it is automatically deregistered, and a security alert is broadcast.

### 1.2 Global Alert Bus
- **Unified Notifications**: A central bus handles system-level events (Discovery, Success, Tamper, Error).
- **HUD Toasts**: Real-time, color-coded notifications appear in the Web UI.
- **Remote Alerts**: Optional integration with `ntfy.sh` for mobile/desktop notifications outside the browser.

---

## 2. Updated Kernel Inventory
All extractors have been renamed to professional identities and updated to the Certified Contract.

| Kernel ID | Identity | Technology |
| :--- | :--- | :--- |
| `com.docvault.pdf.text` | **PDF Text Engine** | Multi-stage (Native -> Tesseract -> Vision) |
| `com.docvault.pdf.images` | **PDF Image Harvester** | Binary asset extraction + Vision analysis |
| `com.docvault.vision.standard` | **Intelligent Image Analyzer** | Scene description + OCR fallback |
| `com.docvault.vision.photo` | **Photo Intelligence Engine** | EXIF Harvesting + GPS Normalization + Vision |
| `com.openai.whisper.large-v3` | **Aural Intelligence Engine** | Whisper large-v3 transcription |
| `com.docvault.aural.metadata` | **Aural Metadata Extractor** | ID3 Tag harvesting + ffprobe technical specs |
| `com.docvault.video.multimodal` | **Multimodal Video Intelligence** | Audio/Visual synthesis (Whisper + Vision AI) |
| `com.docvault.music.collection` | **Music Collection Intelligence** | **FOLDER LEVEL**: Album synthesis & metadata healing |
| `com.microsoft.word.standard` | **Microsoft Word Extractor** | Dual-mode (Native XML + COM Automation) |
| `com.microsoft.excel.standard` | **Microsoft Excel Extractor** | Recursive sheet traversal + TSV reconstruction |
| `com.microsoft.powerpoint.standard` | **Microsoft PowerPoint Extractor** | Structural slide deconstruction |
| `com.docvault.text.plain` | **Plain Text Engine** | Multi-encoding (UTF-8/Latin-1/CP1252) recovery |
| `com.docvault.code.structural` | **Source Code Intelligence** | Lexical analysis (Pygments) + Logic/TODO mapping |
| `com.docvault.media.diagnostics`| **Media Technical Diagnostics** | ffprobe-based bitstream inspection |
| `com.docvault.system.fallback` | **System Fallback Kernel** | Terminal safety tier for unsupported formats |

---

## 3. Core Infrastructure Upgrades
- **Folder Intelligence**: The ingestor and router now support `target_type: folder`, allowing for composite analysis of entire directories (e.g., Music Albums).
- **Binary Subprocess Protocol**: DocVault can now register external executables (Go, Rust, Node.js) as kernels via a standardized JSON manifest and STDOUT handshake.
- **Binary Registration Wizard**: A high-fidelity UI in the Lab for linking and "signing" external binaries without writing code.
- **Database Concurrency**: Enabled **WAL Mode** and implemented batch commits (per 500 records) to prevent "Database Locked" errors during heavy maintenance.

## 4. Current TODO List
- [ ] **Prompt Injection Hardening**: Secure the Vision and Aural LLM stages against adversarial document text.
- [ ] **Identity Hub Enhancements**: Add face crop gallery and interactive merge wizard.
- [ ] **Archive X-Ray**: Implement header-level search for ZIP/TAR archives.
- [ ] **Windows Native HUD**: Build a system tray utility for OS-level notifications.
