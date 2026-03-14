# DocVault Built-in Extractor Catalogue

## 1. Introduction

DocVault ships with built-in kernels covering text documents, images, audio, video, source code, archives, and more. All are enabled by default. Some require external binaries or model downloads — the **Installation** column calls these out.

For the full kernel contract, see [contract.md](contract.md). To write a new kernel, see [writing-an-extractor.md](writing-an-extractor.md).

---

## 2. Catalogue

> Extensions listed are exactly those declared in each kernel's `MANIFEST`. Rows are ordered by domain, then kernel name.

### Text & Documents

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `plaintext_extractor` | `com.docvault.text.plain` | Plain Text Engine | `.txt` `.md` `.csv` `.json` `.yaml` `.toml` `.log` `.html` `.xml` `.py` `.js` `.ts` | Full text, line count | Built-in |
| `text_extractor` | `com.docvault.pdf.text` | PDF Text Engine | `.pdf` | Full text via pdfminer, page count, metadata; requires Tesseract for scanned pages | Requires Tesseract |
| `image_extractor` | `com.docvault.pdf.images` | PDF Image Harvester | `.pdf` | Extracts embedded images from PDF pages and dispatches each to OCR / vision sub-kernels; results indexed and searchable | Built-in (image routing requires Tesseract / Ollama vision) |
| `microsoft_word_extractor` | `com.microsoft.word.standard` | Microsoft Word Extractor | `.docx` `.doc` | Text, heading structure, metadata | Built-in (`pywin32` required for legacy `.doc`) |
| `microsoft_excel_extractor` | `com.microsoft.excel.standard` | Microsoft Excel Extractor | `.xlsx` `.xls` | Cell text, sheet names, row and column counts | Built-in |
| `microsoft_powerpoint_extractor` | `com.microsoft.powerpoint.standard` | Microsoft PowerPoint Extractor | `.pptx` `.ppt` | Slide text, speaker notes, slide count | Built-in |
| `gdrive_extractor` | `com.google.drive.bridge` | Google Drive Bridge | `.gdoc` `.gsheet` `.gslides` `.gform` `.gdraw` | Exported document text via Google Drive API | Requires Google OAuth |

### Source Code

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `source_code_intelligence_extractor` | `com.docvault.code.structural` | Source Code Intelligence | `.py` `.js` `.ts` `.java` `.cpp` `.c` `.go` `.rs` `.rb` `.php` `.sh` `.css` `.html` `.yaml` `.toml` | Code text, language detection via Pygments, structure analysis (classes, functions, imports) | Built-in (`pygments`) |

### Images & Vision

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `intelligent_image_extractor` | `com.docvault.vision.standard` | Intelligent Image Analyzer | `.jpg` `.jpeg` `.png` `.webp` `.bmp` `.tiff` `.tif` | OCR text (Tesseract), image dimensions, colour mode, AI-generated visual description | Requires Tesseract + Ollama vision |
| `photo_intelligence_extractor` | `com.docvault.vision.photo` | Photo Intelligence Engine | `.jpg` `.jpeg` `.png` `.webp` `.heic` | EXIF metadata (GPS, camera model, date/time), AI visual description | Requires Ollama vision |
| `face_analytics_extractor` | `com.docvault.vision.face.analytics` | Face Analytics Engine | `.jpg` `.jpeg` `.png` `.webp` | Face detection, emotion analysis, age/gender estimation via FER + TensorFlow + OpenCV | Requires fer + TensorFlow |
| `face_identity_extractor` | `com.docvault.vision.face` | Face Identity Extractor | `.jpg` `.jpeg` `.png` `.webp` | Face embeddings for identity matching and clustering | Requires face_recognition + OpenCV |
| `face_narrative_intelligence_extractor` | `com.docvault.vision.face.narrative` | Face Narrative Intelligence | `.jpg` `.jpeg` `.png` `.webp` | LLM-generated narrative describing detected faces; reads face analytics results from prior extraction pass | Requires Ollama chat |

### Audio

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `aural_intelligence_extractor` | `com.openai.whisper.large-v3` | Aural Intelligence Engine | `.mp3` `.wav` `.m4a` `.flac` `.ogg` | Full speech-to-text transcription via Whisper | Requires Whisper |
| `aural_metadata_extractor` | `com.docvault.aural.metadata` | Aural Metadata Extractor | `.mp3` `.wav` `.m4a` `.flac` `.ogg` | ID3 / Vorbis tags (title, artist, album, year, track, genre), duration, bitrate via Mutagen + ffprobe | Requires ffmpeg |
| `music_collection_intelligence_extractor` | `com.docvault.music.collection` | Music Collection Intelligence | folder-level (audio folders containing `.mp3` `.wav` `.flac` `.ogg` `.m4a`) | Collection-wide album and library analysis; summarises artist/album distribution, identifies gaps and duplicates | Built-in |

### Video

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `media_technical_diagnostics_extractor` | `com.docvault.media.diagnostics` | Media Diagnostics Engine | `.mp4` `.mov` `.mkv` `.avi` `.webm` `.mp3` `.wav` `.m4a` `.flac` `.ogg` | Codec, bitrate, resolution, duration, full stream map via ffprobe | Requires ffmpeg |
| `multimodal_video_intelligence_extractor` | `com.docvault.video.multimodal` | Multimodal Video Intelligence | `.mp4` `.mov` `.mkv` `.avi` `.webm` | Frame-sampled visual descriptions (Ollama vision), embedded audio transcription (Whisper), technical metadata (ffprobe) | Requires Ollama vision + ffmpeg + Whisper |

### Archives

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `archive_xray_extractor` | `com.docvault.system.archive_xray` | Archive X-Ray Engine | `.zip` `.tar` `.tgz` `.tbz2` `.gz` `.bz2` `.7z` `.rar` | Full file manifest, nested extension summary, total compressed/uncompressed size, directory structure overview | Built-in (`.7z` needs 7-Zip on PATH; `.rar` needs unrar on PATH) |

### System

| Kernel | ID | Name | File types | What it extracts | Installation |
|--------|----|------|-----------|-----------------|--------------|
| `fallback_kernel` | `com.docvault.system.fallback` | System Fallback Kernel | All unrecognised types (`*`) | Records the file as seen; extracts filename, extension, and file size | Built-in |

---

## 3. External Dependency Installation

### Tesseract OCR

Required by: `text_extractor`, `intelligent_image_extractor`

- **Windows:** Download installer from <https://github.com/UB-Mannheim/tesseract/wiki>. After install, note the path (e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`) and set it in `config.ini`:
  ```ini
  [tesseract]
  path = C:\Program Files\Tesseract-OCR\tesseract.exe
  ```
- **Ubuntu/Debian:** `sudo apt install tesseract-ocr`
- **macOS:** `brew install tesseract`

### Poppler (PDF image extraction)

Required by: `image_extractor` (PDF Image Harvester uses `pdf2image` which wraps Poppler)

- **Windows:** Download from <https://github.com/oschwartz10612/poppler-windows>, extract, then set in `config.ini`:
  ```ini
  [pdf]
  poppler_path = C:\path\to\poppler\Library\bin
  ```
- **Ubuntu/Debian:** `sudo apt install poppler-utils`
- **macOS:** `brew install poppler`

### ffmpeg / ffprobe

Required by: `aural_metadata_extractor`, `media_technical_diagnostics_extractor`, `multimodal_video_intelligence_extractor`

- **Windows:** Download from <https://ffmpeg.org/download.html>, extract, and add the `bin/` folder to your system `PATH`
- **Ubuntu/Debian:** `sudo apt install ffmpeg`
- **macOS:** `brew install ffmpeg`

### Whisper (audio transcription)

Required by: `aural_intelligence_extractor`, `multimodal_video_intelligence_extractor`

Already listed in `requirements.txt` as `openai-whisper`. Models are downloaded on first use to `~/.cache/whisper/`.

| Model | Size | Speed (CPU) | Recommended for |
|-------|------|-------------|----------------|
| `base` | 140 MB | ~10× real-time | CPU-only machines |
| `large-v3` | 2.9 GB | near real-time (GPU) | GPU machines |

First run may take 30–120 seconds while the model loads into memory. The default model is configured via `system:whisper_model` in Settings.

### Ollama (vision and chat models)

Required by: `intelligent_image_extractor`, `photo_intelligence_extractor`, `multimodal_video_intelligence_extractor`, `face_narrative_intelligence_extractor`, `source_code_intelligence_extractor` (some analysis paths)

1. Install Ollama from <https://ollama.com>
2. Pull the required models:
   ```bash
   ollama pull minicpm-v      # vision (default)
   ollama pull deepseek-r1:14b  # chat / reasoning (default)
   ```
3. Confirm Ollama is running at `http://localhost:11434` (configurable via `ollama:url` in Settings)

### face_recognition / dlib (face identity)

Required by: `face_identity_extractor`

Requires a C++ compiler and CMake before pip install.

- **Windows:** Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) first, then:
  ```
  venv\Scripts\pip install face_recognition
  ```
- **Linux/macOS:**
  ```bash
  pip install face_recognition
  ```

### fer + TensorFlow (face analytics)

Required by: `face_analytics_extractor`

```bash
# Windows
venv\Scripts\pip install fer tensorflow-cpu opencv-python

# Linux/macOS
pip install fer tensorflow-cpu opencv-python
```

`fer` depends on TensorFlow — a large download (several hundred MB) on first install. Use `tensorflow-cpu` unless you have a CUDA-capable GPU.

### Google Drive OAuth

Required by: `gdrive_extractor`

1. Create a project in [Google Cloud Console](https://console.cloud.google.com) and enable the Google Drive API
2. Download OAuth 2.0 credentials and save as `credentials/google_credentials.json` (this directory is gitignored)
3. On first use, DocVault opens a browser auth flow; the resulting token is cached in `credentials/google_token.json`

---

## 4. Notes on Overlapping Kernels

Several kernels cover the same file types and operate at different depths. DocVault runs all applicable kernels for each file; results are merged into the document record.

| File type | Kernels that run | Notes |
|-----------|-----------------|-------|
| `.pdf` | `text_extractor` + `image_extractor` | Text engine handles text layer; Image Harvester extracts embedded images and sub-routes them |
| `.jpg` / `.png` / `.webp` | `intelligent_image_extractor` + `photo_intelligence_extractor` + `face_analytics_extractor` + `face_identity_extractor` + `face_narrative_intelligence_extractor` | Each adds a distinct layer (OCR, EXIF, emotion, identity, narrative) |
| `.mp3` / `.flac` etc. | `aural_intelligence_extractor` + `aural_metadata_extractor` + `media_technical_diagnostics_extractor` | Transcription, tags, and technical stream info respectively |
| `.mp4` / `.mkv` etc. | `multimodal_video_intelligence_extractor` + `media_technical_diagnostics_extractor` | Intelligence engine includes ffprobe metadata; Diagnostics engine is a lighter-weight fallback |
| `.py` / `.js` / `.html` etc. | `plaintext_extractor` + `source_code_intelligence_extractor` | Plain Text Engine captures raw text; Source Code Intelligence adds language-aware structure analysis |
