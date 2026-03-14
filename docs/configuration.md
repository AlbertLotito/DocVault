# DocVault Configuration Reference

## 1. Introduction — Settings Resolution Chain

DocVault resolves settings in priority order (highest first):

1. **vault_settings** — per-vault overrides stored in `settings.db`
2. **settings.db** — user-set values via the Settings UI
3. **config.ini** — deployment defaults written by `setup.ps1`
4. **Schema default** — built-in fallback

**Two ways to change settings:**

- Edit `config.ini` for deployment defaults (affects all vaults unless overridden)
- Use the Settings page in the web UI to write to `settings.db` (survives `reset.ps1`, takes priority over `config.ini`)

---

## 2. Full Settings Reference

### [paths]

| Key | Default | Description |
|-----|---------|-------------|
| `scan_directory` | *(required)* | Root directory to scan for documents. Set by `setup.ps1`. |
| `cache_directory` | `<repo>/.cache/extracted_images` | Where extracted image thumbnails are stored. Regenerable — safe to delete. |

### [database]

| Key | Default | Description |
|-----|---------|-------------|
| `sqlite_path` | `<repo>/docvault.db` | Path to the main operational database. |

### [llm]

| Key | Default | Description |
|-----|---------|-------------|
| `provider` | `ollama` | LLM backend. Only `ollama` is currently supported. |

### [tesseract]

| Key | Default | Description |
|-----|---------|-------------|
| `path` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | Absolute path to the Tesseract binary. Linux/macOS: `/usr/bin/tesseract`. |

### [ollama]

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `http://localhost:11434` | Ollama server URL. |
| `chat_model` | `deepseek-r1:14b` | Model used for RAG and chat. |
| `embed_model` | `nomic-embed-text` | Model used for semantic embeddings. |
| `num_ctx` | `8192` | Context window size for chat. |
| `temperature` | `0.1` | Sampling temperature (0.0–1.0). Lower = more deterministic. |
| `num_predict` | `-1` | Max tokens to generate. `-1` = model default. |
| `top_p` | `0.9` | Top-p nucleus sampling parameter. |
| `repeat_penalty` | `1.1` | Penalty for repeated tokens. |
| `max_parallel` | `1` | Max concurrent Ollama requests. Increase with caution on GPU. |

### [qdrant]

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `localhost` | Qdrant server host. |
| `port` | `6333` | Qdrant server port. |

### [pdf]

| Key | Default | Description |
|-----|---------|-------------|
| `poppler_path` | `<repo>\bin\poppler\Library\bin` | Path to the Poppler `bin` directory. Linux/macOS: leave empty if Poppler is on PATH. |
| `sparse_threshold` | `50` | Character count below which a PDF page is considered image-only and sent to OCR. |

### [vision]

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `minicpm-v` | Ollama vision model for image description. |
| `describe_images` | `true` | Enable AI image description. Set `false` to skip vision AI (faster ingestion). |

### [embeddings]

| Key | Default | Description |
|-----|---------|-------------|
| `chunk_size` | `600` | Characters per embedding chunk. |
| `chunk_overlap` | `100` | Character overlap between consecutive chunks. |
| `score_threshold` | `0.65` | Minimum semantic similarity score (0.0–1.0) for results to appear. |

### [search]

| Key | Default | Description |
|-----|---------|-------------|
| `rag_top_k` | `5` | Number of chunks retrieved for RAG context. |
| `rag_threshold` | `0.50` | Minimum similarity for RAG chunks. |
| `result_limit` | `20` | Max search results returned per query. |
| `fts_weight` | `0.4` | FTS weight in hybrid search scoring (must sum to 1.0 with `sem_weight`). |
| `sem_weight` | `0.6` | Semantic weight in hybrid search scoring. |

### [video]

| Key | Default | Description |
|-----|---------|-------------|
| `describe_frames` | `true` | Enable AI frame-by-frame visual description for videos. |
| `frame_interval` | `10` | Sample one frame every N seconds for visual description. |

### [monitor]

| Key | Default | Description |
|-----|---------|-------------|
| `search_throttle_duration` | `30` | Seconds to pause extraction workers after a user search (to free resources). |
| `stuck_task_threshold_mins` | `30` | Minutes before a PROCESSING task is flagged as stuck in the health check. |

### [google]

| Key | Default | Description |
|-----|---------|-------------|
| `credentials_path` | `<repo>\credentials\google_credentials.json` | Google OAuth credentials file (optional — only for Google Drive extraction). |
| `token_path` | `<repo>\credentials\google_token.json` | Cached OAuth token path. |

### [alerts]

| Key | Default | Description |
|-----|---------|-------------|
| `ntfy_url` | *(empty)* | Optional ntfy.sh push notification URL. Leave empty to disable. |

### [system]

| Key | Default | Description |
|-----|---------|-------------|
| `debug_mode` | `false` | Enable verbose INFO/DEBUG logging to the console. |

> **Platform note:** Settings using Windows-specific sensors (`wmi` for CPU temperature, `nvidia-ml-py` for GPU monitoring) degrade gracefully on Linux/macOS — the affected sensor is replaced by a no-op stub and all other settings remain active.

---

## 3. settings.db Overrides

The Settings page in the web UI writes directly to `settings.db`. These values:

- Take priority over `config.ini`
- Survive `reset.ps1` (which never touches `settings.db`)
- Apply system-wide unless a vault-level override exists

To clear a `settings.db` override: set the value back via the Settings UI, or connect to `settings.db` with any SQLite client and delete the relevant row from the `settings` table.

---

## 4. Per-Vault Overrides

Individual vaults can override any setting. Vault-level overrides are managed from the vault settings panel in the UI and stored in the `vault_settings` table in `settings.db`.

Example use case: one vault scans a slow network share — set `describe_images = false` for that vault to skip vision AI without affecting other vaults.
