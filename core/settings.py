import configparser
import os

class Settings:
    def __init__(self, config_path):
        self.config = configparser.ConfigParser(strict=False)
        self.config.read(config_path)
        
        # This defines the settings that are user-configurable via the UI.
        # 'group' controls which tab the setting appears in.
        self.schema = {
            # General
            'paths:scan_directory': {
                'type': 'string', 'default': '.', 'label': 'Scan Directory', 'group': 'general',
                'description': 'Root folder DocVault monitors for documents. All files found recursively under this path are queued for extraction and embedding. Changing this does not remove already-indexed files — it only affects what gets discovered on the next scan.',
            },
            'paths:cache_directory': {
                'type': 'string', 'default': '', 'label': 'Cache Directory', 'group': 'general',
                'description': 'Folder where extracted artefacts (e.g. images pulled from PDFs) are stored. These files are indexed and searchable, but kept separate from your source documents to avoid clutter. Leave blank to use the default: <app folder>\\.cache\\extracted_images.',
            },
            'llm:provider': {
                'type': 'string', 'default': 'ollama', 'label': 'LLM Provider', 'group': 'general',
                'description': 'Which LLM backend powers the "Ask" chat. Currently supports "ollama" (local, via Ollama) and "claude" (Anthropic API). A server restart is required after changing this.',
            },
            'tesseract:path': {
                'type': 'string', 'default': r'C:\Program Files\Tesseract-OCR\tesseract.exe', 'label': 'Tesseract Path', 'group': 'general',
                'description': 'Full path to the Tesseract OCR executable. Used as a fallback when the vision model is unavailable or disabled. The default Windows install path is C:\\Program Files\\Tesseract-OCR\\tesseract.exe.',
            },
            # Ollama
            'ollama:host': {
                'type': 'string', 'default': 'http://localhost:11434', 'label': 'Host', 'group': 'ollama',
                'description': 'Base URL for the Ollama API server. Change this if Ollama runs on a different machine or a non-default port.',
            },
            'ollama:chat_model': {
                'type': 'string', 'default': 'qwen2.5:14b', 'label': 'Chat Model', 'group': 'ollama',
                'description': 'Ollama model used for RAG answers in the "Ask" chat. Must be pulled first with "ollama pull <model>". Recommended: qwen2.5:14b (speed/quality balance) or qwen2.5:32b for higher quality.',
            },
            'ollama:embed_model': {
                'type': 'string', 'default': 'nomic-embed-text', 'label': 'Embedding Model', 'group': 'ollama',
                'description': 'Ollama model used to generate vector embeddings for semantic search. Changing this invalidates all existing embeddings — use Rebuild Index to re-embed everything. Default: nomic-embed-text.',
            },
            'ollama:embed_timeout': {
                'type': 'int', 'default': 120, 'label': 'Embedding Timeout (s)', 'group': 'ollama',
                'description': 'HTTP timeout in seconds for Ollama embedding calls. '
                               'Prevents indefinite hangs when Ollama is unresponsive.',
            },
            'ollama:chat_timeout': {
                'type': 'int', 'default': 300, 'label': 'Chat Timeout (s)', 'group': 'ollama',
                'description': 'HTTP timeout in seconds for Ollama chat/RAG calls. '
                               'If the model does not respond within this window the query returns an error.',
            },
            'ollama:slot_timeout': {
                'type': 'int', 'default': 30, 'label': 'Slot Wait Timeout (s)', 'group': 'ollama',
                'description': 'How long to wait for a chat concurrency slot before failing with an error. '
                               'Prevents new queries from queuing silently behind a stuck request.',
            },
            'ollama:query_embed_timeout': {
                'type': 'int', 'default': 15, 'label': 'Query Embed Timeout (s)', 'group': 'ollama',
                'description': 'HTTP timeout for query-time embedding calls. Kept short so a busy Ollama '
                               'fails fast and the search falls back to FTS-only rather than blocking.',
            },
            'ollama:embed_slot_timeout': {
                'type': 'int', 'default': 10, 'label': 'Embed Slot Timeout (s)', 'group': 'ollama',
                'description': 'How long a search query waits for the embed semaphore before giving up '
                               'and falling back to FTS-only results. Prevents queries hanging while '
                               'the background embedding worker processes a large batch.',
            },
            'ollama:num_ctx': {
                'type': 'int', 'default': 8192, 'label': 'Context Window (tokens)', 'group': 'ollama',
                'description': 'Maximum number of tokens the model can see at once, including retrieved chunks and conversation history. Larger values use more VRAM. Must not exceed the model\'s own maximum context length.',
            },
            'ollama:temperature': {
                'type': 'float', 'default': 0.1, 'label': 'Temperature', 'group': 'ollama',
                'description': 'Controls response randomness. 0 = fully deterministic and precise, 1 = highly creative. For document Q&A, keep this low (0.0–0.2) to get factual, consistent answers.',
            },
            'ollama:num_predict': {
                'type': 'int', 'default': -1, 'label': 'Max Tokens to Generate', 'group': 'ollama',
                'description': 'Maximum number of tokens the model will generate per response. -1 means unlimited (the model stops when it decides the answer is complete). Set a positive number to cap response length.',
            },
            'ollama:top_p': {
                'type': 'float', 'default': 0.9, 'label': 'Top-P', 'group': 'ollama',
                'description': 'Nucleus sampling: only tokens whose cumulative probability reaches this threshold are considered. Lower values (e.g. 0.5) make responses more focused; higher values (e.g. 0.95) allow more variety. Works together with Temperature.',
            },
            'ollama:repeat_penalty': {
                'type': 'float', 'default': 1.1, 'label': 'Repeat Penalty', 'group': 'ollama',
                'description': 'Penalises the model for repeating tokens it has recently generated. Values above 1.0 discourage repetition. Increase slightly (e.g. 1.2–1.3) if answers loop or repeat phrases.',
            },
            'ollama:max_parallel': {
                'type': 'int', 'default': 1, 'label': 'Max Parallel Requests', 'group': 'ollama',
                'description': 'Maximum number of concurrent requests sent to Ollama. Set to 1 to serialize all requests (prevents "no slots available" errors). Increase this if you have configured Ollama with OLLAMA_NUM_PARALLEL > 1 and have sufficient VRAM. Changes take effect immediately — no restart required.',
            },
            # LanceDB
            'lancedb:path': {
                'type': 'string', 'default': 'lancedb_storage', 'label': 'LanceDB storage path', 'group': 'lancedb',
                'description': 'Directory where LanceDB stores vector data. Relative to project root.',
            },
            # PDF extraction
            'pdf:poppler_path': {
                'type': 'string', 'default': '', 'label': 'Poppler bin path', 'group': 'pdf',
                'description': 'Path to the Poppler "bin" folder (the one containing pdftoppm.exe). Required for rendering PDF pages as images for OCR and vision processing. Download from github.com/oschwartz10612/poppler-windows and point this at the Library\\bin subfolder.',
            },
            'pdf:sparse_threshold': {
                'type': 'int', 'default': 50, 'label': 'Sparse page threshold (chars)', 'group': 'pdf',
                'description': 'A PDF page with fewer extracted characters than this value is considered "sparse" (likely scanned or image-based) and will be re-processed via image rendering and OCR/vision. Increase to push more pages through the image pipeline; decrease to trust the native text layer more.',
            },
            'pdf:min_image_area': {
                'type': 'int', 'default': 10000, 'label': 'Min image area (px²)', 'group': 'pdf',
                'description': 'Minimum pixel area (width × height) for an embedded PDF image to be extracted and analysed. Images below this threshold are likely decorative (icons, bullets, dividers) and are skipped. Default: 10,000 (≈ 100×100px).',
            },
            'pdf:max_images_per_pdf': {
                'type': 'int', 'default': 50, 'label': 'Max images per PDF', 'group': 'pdf',
                'description': 'Maximum number of embedded images extracted per PDF file. Set to 0 for no limit. Prevents a single image-heavy PDF from monopolising the extraction worker. Images beyond the cap are not saved or analysed.',
            },
            'swf:min_image_area': {
                'type': 'int', 'default': 160000, 'label': 'SWF min image area (px²)', 'group': 'swf',
                'description': "Minimum pixel area (width × height) for an embedded SWF image to be OCR'd. Images below this are UI chrome (buttons, icons). Default: 160,000 (≈ 400×400px).",
            },
            'swf:max_pages': {
                'type': 'int', 'default': 200, 'label': 'SWF max pages', 'group': 'swf',
                'description': 'Maximum number of images to OCR per SWF file. Larger newspapers may have more pages — raise if needed.',
            },
            # Vision
            'vision:model': {
                'type': 'string', 'default': 'minicpm-v', 'label': 'Vision Model', 'group': 'vision',
                'description': 'Ollama vision model used to describe standalone images and transcribe scanned PDF pages. Must support image input. Pull it first: "ollama pull minicpm-v". Alternatives: llava, llava-llama3.',
            },
            'vision:describe_images': {
                'type': 'string', 'default': 'true', 'label': 'Describe images', 'group': 'vision',
                'description': 'Set to "true" to enable vision-based description for image files and images embedded in PDFs. Set to "false" to disable — Tesseract OCR will still run as a fallback for standalone images. Disable to speed up extraction if you do not need image understanding.',
            },
            'vision:timeout_secs': {
                'type': 'int', 'default': 240, 'label': 'Vision timeout (s)', 'group': 'vision',
                'description': 'Maximum seconds to wait for the Ollama vision model to respond. If the model takes longer (e.g. due to cold-start VRAM loading), the vision step is skipped and extraction continues with EXIF/OCR data only. Default: 240. Increase if you have a slow GPU or a very large model.',
            },
            # Embeddings
            'embeddings:chunk_size': {
                'type': 'int', 'default': 1000, 'label': 'Chunk size (chars)', 'group': 'embeddings',
                'description': 'Maximum number of characters per text chunk. The chunker fills chunks with whole sentences up to this limit. Smaller chunks produce more precise matches; larger chunks give the model more context. Recommended range: 800–1500. Changing this requires Rebuild Index.',
            },
            'embeddings:chunk_overlap': {
                'type': 'int', 'default': 200, 'label': 'Chunk overlap (chars)', 'group': 'embeddings',
                'description': 'Trailing sentences carried into the next chunk to preserve context across boundaries. Recommended: 15–25% of chunk size. Changing this requires Rebuild Index.',
            },
            'embeddings:score_threshold': {
                'type': 'float', 'default': 0.65, 'label': 'Search score threshold', 'group': 'embeddings',
                'description': 'Minimum cosine similarity score (0.0–1.0) for a chunk to appear in search results on the Search page. Higher values return only strong matches and reduce noise. Does not affect the RAG chat threshold (see Search tab).',
            },
            # Worker throughput
            'workers:embed_batch_size': {
                'type': 'int', 'default': 16, 'label': 'Embed batch size (docs)', 'group': 'embeddings',
                'description': 'Number of documents whose chunks are accumulated into a single Ollama embedding call. '
                               'Larger values keep the GPU busier between documents but use more memory. '
                               'Recommended: 8–32. Has no effect when the queue is nearly empty.',
            },
            'workers:embed_chunk_limit': {
                'type': 'int', 'default': 250, 'label': 'Embed chunk limit (per Ollama call)', 'group': 'embeddings',
                'description': 'Maximum number of chunks sent to Ollama in a single embed call. '
                               'Keeping this low prevents Ollama timeouts and leaves headroom for RAG queries. '
                               'Recommended: 100–300.',
            },
            'workers:embed_concurrency': {
                'type': 'int', 'default': 1, 'label': 'Embed worker threads', 'group': 'embeddings',
                'description': 'Number of parallel embedding worker threads. Each worker claims its own task batch '
                               'and runs a concurrent Ollama embed call. Requires OLLAMA_NUM_PARALLEL > 1 '
                               'and sufficient VRAM. The embed semaphore is sized to this value at startup — '
                               'restart required for changes to take effect.',
            },
            'workers:index_rebuild_every': {
                'type': 'int', 'default': 100, 'label': 'Index rebuild interval (batches)', 'group': 'embeddings',
                'description': 'Rebuild the LanceDB vector index every N embedding batches to keep ANN search fast '
                               'as new vectors accumulate. Each rebuild runs in a background thread and takes '
                               'several minutes for large tables. Set higher to reduce rebuild frequency.',
            },
            'workers:extract_age_weight': {
                'type': 'int', 'default': 3600,
                'label': 'Extraction age weight (s per priority point)', 'group': 'embeddings',
                'description': 'Seconds a PENDING task must wait to gain 1 priority point via aging. '
                               'Lower = ages faster. Default 3600 = 1 hour per point.',
            },
            'workers:embed_age_weight': {
                'type': 'int', 'default': 900,
                'label': 'Embedding age weight (s per priority point)', 'group': 'embeddings',
                'description': 'Seconds an EXTRACTED task must wait to gain 1 priority point via aging. '
                               'Lower = ages faster. Default 900 = 15 minutes per point.',
            },
            # Search
            'search:rag_top_k': {
                'type': 'int', 'default': 5, 'label': 'RAG chunks', 'group': 'search',
                'description': 'Number of document chunks retrieved from the vector store and sent to the LLM when answering a question. More chunks = more context for the LLM but slower responses and higher token usage. Recommended: 5–10.',
            },
            'search:rag_threshold': {
                'type': 'float', 'default': 0.50, 'label': 'RAG score threshold', 'group': 'search',
                'description': 'Minimum cosine similarity score for a chunk to be included in RAG context. Intentionally lower than the search display threshold so the LLM sees a wider range of potentially relevant chunks — the LLM judges final relevance. Recommended: 0.45–0.55.',
            },
            'search:result_limit': {
                'type': 'int', 'default': 20, 'label': 'Max search results', 'group': 'search',
                'description': 'Maximum number of results shown on the Search page. Applies to full-text, semantic, and hybrid search modes.',
            },
            'search:fts_weight': {
                'type': 'float', 'default': 0.4, 'label': 'Hybrid FTS weight', 'group': 'search',
                'description': 'Score weight given to full-text search results in hybrid mode. Hybrid search combines FTS and semantic scores; the two weights should sum to 1.0. Increase this to favour exact keyword matches over conceptual similarity.',
            },
            'search:sem_weight': {
                'type': 'float', 'default': 0.6, 'label': 'Hybrid semantic weight', 'group': 'search',
                'description': 'Score weight given to semantic (vector) search results in hybrid mode. Increase this to favour meaning-based matches — useful for natural-language queries and paraphrased content.',
            },
            # Video
            'video:describe_frames': {
                'type': 'string', 'default': 'true', 'label': 'Describe video frames', 'group': 'video',
                'description': 'Set to "true" to extract frames from video files and describe them with the vision model. Combines visual descriptions with the Whisper audio transcript for richer video search. Set to "false" to use audio transcription only. Requires vision:describe_images to also be enabled.',
            },
            'video:frame_interval': {
                'type': 'int', 'default': 10, 'label': 'Frame interval (seconds)', 'group': 'video',
                'description': 'How often to sample a frame from the video, in seconds. Lower values produce more descriptions but take longer. For a 5-minute video at 10s intervals = ~30 frames. For long videos (>30 min) consider raising this to 30–60 seconds.',
            },
            # Google Vision API (Art Enrichment)
            'google:vision_api_key': {
                'type': 'string', 'default': '', 'label': 'Vision API Key', 'group': 'google',
                'description': 'Google Cloud Vision API key for art identification. Required by the Art Enrichment worker (Tier 3) to identify artworks, fill metadata, and rename image files. Get a key from Google Cloud Console → APIs & Services → Credentials.',
            },
            # Art Collection Enrichment
            'art:enrichment_requests_per_minute': {
                'type': 'int', 'default': 5, 'label': 'Requests per minute', 'group': 'art',
                'description': 'Maximum Google Vision API calls per minute during art enrichment. Keep this low to stay within free-tier quotas and be respectful to the API. Default: 5.',
            },
            'art:enrichment_confidence_threshold': {
                'type': 'float', 'default': 0.85, 'label': 'Rename confidence threshold', 'group': 'art',
                'description': 'Minimum confidence score (0.0–1.0) required before the enrichment worker renames an image file to "<Artist> - <Title>.<ext>". Below this threshold the .nfo sidecar is written but the file is left as-is for manual review. Default: 0.85.',
            },
            'art:enrichment_retry_hours': {
                'type': 'int', 'default': 24, 'label': 'Retry failed lookups after (hours)', 'group': 'art',
                'description': 'How many hours to wait before retrying a failed identification lookup. Failed images have renamed_to=(failed) in their .nfo sidecar. Default: 24.',
            },
            # Art — Tier 1: Local CLIP
            'art:clip_enabled': {
                'type': 'string', 'default': 'true', 'label': 'Enable local CLIP lookup', 'group': 'art',
                'description': 'Set to "true" to query a local LanceDB art index using CLIP embeddings before calling any cloud API. Zero cost and fully private. Requires the art index to be pre-built (see docs). If the index is absent, this tier is silently skipped.',
            },
            'art:clip_table': {
                'type': 'string', 'default': 'art_index', 'label': 'Art index table name', 'group': 'art',
                'description': 'Name of the LanceDB table (in lancedb_storage/) that holds the pre-indexed art dataset (WikiArt, MET, etc.). Must be populated separately before CLIP lookup will return results.',
            },
            'art:clip_accept_threshold': {
                'type': 'float', 'default': 0.80, 'label': 'CLIP accept threshold', 'group': 'art',
                'description': 'Minimum CLIP cosine similarity (0.0–1.0) to accept a local match and skip cloud lookup entirely. Higher values are more conservative. Default: 0.80.',
            },
            'art:clip_fallback_threshold': {
                'type': 'float', 'default': 0.70, 'label': 'CLIP fallback threshold', 'group': 'art',
                'description': 'If CLIP confidence is below this value, cloud lookup is attempted as a fallback. Set equal to clip_accept_threshold to always try cloud when CLIP confidence is not high enough to accept. Default: 0.70.',
            },
            # Art — Tier 2: Cloud
            'art:cloud_provider': {
                'type': 'string', 'default': 'google', 'label': 'Primary cloud provider', 'group': 'art',
                'description': 'Which cloud API to call first for art identification. Options: "google" (Google Vision WEB_DETECTION), "bing" (Bing Visual Search). The other provider is used as automatic fallback if the primary returns low confidence and both keys are configured.',
            },
            'art:cloud_fallback_threshold': {
                'type': 'float', 'default': 0.70, 'label': 'Cloud fallback threshold', 'group': 'art',
                'description': 'If the primary cloud provider returns confidence below this value and the secondary provider is configured, the secondary is tried automatically. Default: 0.70.',
            },
            'art:google_monthly_limit': {
                'type': 'int', 'default': 1000, 'label': 'Art identification monthly limit', 'group': 'google',
                'description': 'Maximum Google Vision API calls per calendar month for art identification. When this limit is reached, Google is skipped and Bing is used as fallback (if configured). Set to 0 to disable Google entirely. Resets on the 1st of each month.',
            },
            'art:bing_monthly_limit': {
                'type': 'int', 'default': 1000, 'label': 'Art identification monthly limit', 'group': 'bing',
                'description': 'Maximum Bing Visual Search API calls per calendar month for art identification. When this limit is reached, Bing is skipped for the rest of the month. Set to 0 to disable Bing entirely. Resets on the 1st of each month.',
            },
            # Google Drive
            'google:credentials_path': {
                'type': 'string', 'default': '', 'label': 'Credentials JSON path', 'group': 'google',
                'description': 'Path to the OAuth 2.0 client credentials JSON file downloaded from Google Cloud Console. Required to access Google Drive files (.gdoc, .gsheet, .gslides). Download from: APIs & Services → Credentials → your Desktop app → Download JSON.',
            },
            'google:token_path': {
                'type': 'string', 'default': '', 'label': 'Token JSON path', 'group': 'google',
                'description': 'Path where the OAuth access token will be saved after the first authorisation. This file is created automatically when you authorise DocVault via the Utilities page. Keep it in the credentials folder alongside the credentials JSON.',
            },
            # HuggingFace
            'huggingface:access_token': {
                'type': 'string', 'default': '', 'label': 'Access Token', 'group': 'huggingface',
                'description': 'HuggingFace access token. Used when downloading datasets (e.g. WikiArt for the art index). Not required for public datasets but prevents auth prompts if a dataset becomes gated. Generate one at huggingface.co/settings/tokens.',
            },
            # Bing Visual Search (Art Enrichment)
            'bing:visual_search_api_key': {
                'type': 'string', 'default': '', 'label': 'Visual Search API Key', 'group': 'bing',
                'description': 'Microsoft Bing Visual Search API key. Used as an alternative or fallback cloud provider for art identification. Get a key from Azure Portal → Cognitive Services → Bing Search v7. Leave blank to disable Bing.',
            },
            # Monitor / Resource Governor
            'monitor:enabled': {
                'type': 'string', 'default': 'true', 'label': 'Enable governor', 'group': 'monitor',
                'description': 'Set to "false" to disable the resource governor entirely. Workers will run at full speed regardless of hardware temperatures.',
            },
            'monitor:sample_interval': {
                'type': 'int', 'default': 60, 'label': 'Sample interval (s)', 'group': 'monitor',
                'description': 'How often (in seconds) the governor samples hardware metrics. Lower values give faster response but add slight CPU overhead.',
            },
            'monitor:gpu_temp_throttle': {
                'type': 'float', 'default': 80.0, 'label': 'GPU temp throttle (°C)', 'group': 'monitor',
                'description': 'GPU temperature at which workers slow down (throttled state). A 5-second inter-task delay is added.',
            },
            'monitor:gpu_temp_cooldown': {
                'type': 'float', 'default': 88.0, 'label': 'GPU temp cooldown (°C)', 'group': 'monitor',
                'description': 'GPU temperature at which workers pause completely (cooldown state).',
            },
            'monitor:gpu_util_throttle': {
                'type': 'float', 'default': 70.0, 'label': 'GPU util throttle (%)', 'group': 'monitor',
                'description': 'GPU utilization percentage at which workers throttle.',
            },
            'monitor:sustained_minutes': {
                'type': 'int', 'default': 3, 'label': 'Sustained minutes', 'group': 'monitor',
                'description': 'How many consecutive minutes of throttle-level pressure triggers the cooldown state.',
            },
            'monitor:cooldown_minutes': {
                'type': 'int', 'default': 10, 'label': 'Cooldown minutes', 'group': 'monitor',
                'description': 'How many minutes workers pause during cooldown before retesting.',
            },
            'monitor:ram_throttle_pct': {
                'type': 'float', 'default': 85.0, 'label': 'RAM throttle (%)', 'group': 'monitor',
                'description': 'RAM usage percentage at which workers throttle.',
            },
            'monitor:ram_chain_abort_pct': {
                'type': 'float', 'default': 88.0, 'label': 'RAM chain abort (%)', 'group': 'monitor',
                'description': 'RAM usage percentage at which the extractor chain is aborted before starting the next extractor. If triggered before any extractor has run the task is re-queued; otherwise partial results are saved. Set higher than ram_throttle_pct, lower than ram_ollama_block_pct.',
            },
            'monitor:ram_ollama_block_pct': {
                'type': 'float', 'default': 90.0, 'label': 'RAM Ollama block (%)', 'group': 'monitor',
                'description': 'RAM usage percentage at which chat/vision Ollama calls are blocked with a catchable MemoryError before the request is sent. Prevents the model response buffer from pushing an already-full system over the edge. Should sit between ram_chain_abort_pct and ram_emergency_pct.',
            },
            'monitor:ram_emergency_pct': {
                'type': 'float', 'default': 92.0, 'label': 'RAM emergency stop (%)', 'group': 'monitor',
                'description': 'RAM usage percentage at which workers stop claiming new tasks immediately, bypassing the 60-second monitor sample cycle. Acts as a last-resort circuit breaker before the OS runs out of memory entirely.',
            },
            'monitor:cpu_temp_throttle': {
                'type': 'float', 'default': 85.0, 'label': 'CPU temp throttle (°C)', 'group': 'monitor',
                'description': 'CPU temperature at which workers throttle.',
            },
            'monitor:disk_free_pct_throttle': {
                'type': 'float', 'default': 10.0, 'label': 'Disk free % throttle', 'group': 'monitor',
                'description': 'Throttle workers when disk free space falls below this percentage. Uses OR logic with the GB threshold — either condition triggers throttling.',
            },
            'monitor:disk_free_gb_throttle': {
                'type': 'float', 'default': 5.0, 'label': 'Disk free GB throttle', 'group': 'monitor',
                'description': 'Throttle workers when disk free space falls below this many gigabytes. Uses OR logic with the % threshold — either condition triggers throttling.',
            },
            'monitor:search_throttle_duration': {
                'type': 'int', 'default': 60, 'label': 'Search throttle (s)', 'group': 'monitor',
                'description': 'How many seconds to throttle extractors after a user performs a search. This ensures the UI remains snappy during heavy background processing.',
            },
            'monitor:stuck_task_threshold_mins': {
                'type': 'int', 'default': 10, 'label': 'Stuck task threshold (m)', 'group': 'monitor',
                'description': 'How many minutes a task can remain in PROCESSING or EMBEDDING state without an update before it is flagged as "stuck". Lower values catch dead processes faster; higher values avoid false positives for very large files.',
            },
            'monitor:stall_threshold_mins': {
                'type': 'int', 'default': 30, 'label': 'Stall threshold (m)', 'group': 'monitor',
                'description': 'How many minutes without any extraction activity (while PENDING tasks exist) before the pipeline is flagged as stalled. A stall alert is sent via ntfy.sh and the sensor rail turns red.',
            },
            'monitor:embed_stall_threshold_mins': {
                'type': 'int', 'default': 10, 'label': 'Embed stall threshold (m)', 'group': 'monitor',
                'description': 'Minutes without embedding activity (while EXTRACTED tasks exist) before the embedding pipeline is flagged as stalled.',
            },
            'monitor:watchdog_interval': {
                'type': 'int', 'default': 30, 'label': 'Watchdog interval (s)', 'group': 'monitor',
                'description': 'How often (in seconds) the watchdog checks that all worker threads are alive. Dead threads are restarted automatically and an alert is sent.',
            },
            # Ingestion filtering
            'ingestion:ignore_extensions': {
                'type': 'string', 'default': '.bak, .tmp, .log',
                'label': 'Global ignore extensions', 'group': 'ingestion',
                'description': 'Comma-separated file extensions to skip during ingestion across all vaults. '
                               'Include the dot: .bak, .tmp, .log',
            },
            'ingestion:ignore_folders': {
                'type': 'string', 'default': 'temp*, __pycache__, .git',
                'label': 'Global ignore folders', 'group': 'ingestion',
                'description': 'Comma-separated glob patterns matched against folder name (not full path). '
                               'Examples: temp*, node_modules, *_data',
            },
            # System
            'system:debug_mode': {
                'type': 'string', 'default': 'false', 'label': 'Debug Mode', 'group': 'general',
                'description': 'When enabled, the console output will include all logs (INFO, DEBUG). When disabled, only WARNINGS and ERRORS are shown. Useful for troubleshooting but can be noisy.',
            },
            # Alerts
            'alerts:ntfy_url': {
                'type': 'string', 'default': '', 'label': 'ntfy.sh URL', 'group': 'general',
                'description': 'Optional: A ntfy.sh topic URL (e.g. https://ntfy.sh/my-private-topic) to receive system alerts on your phone or desktop.',
            },
            # Extractor Lab
            'lab:test_timeout_secs': {
                'type': 'int', 'default': 300, 'label': 'Lab test timeout (s)', 'group': 'lab',
                'description': 'Maximum seconds the Extractor Lab will wait for a kernel test to complete before returning a timeout error. Increase this for AI kernels (Whisper, vision, face) that load large models on first use. Default: 300 (5 minutes).',
            },
            'tuning:benchmark_samples_per_type': {
                'type': 'int', 'default': 10,
                'label': 'Benchmark samples per type', 'group': 'lab',
                'description': 'Number of COMPLETED files per type sampled during benchmark runs and optimizer mini-benchmarks. Random selection, no fixed seed.',
            },
            # Server
            'server:host': {
                'type': 'string', 'default': '127.0.0.1', 'label': 'Bind Address', 'group': 'server',
                'description': 'IP address the web server listens on. Use 127.0.0.1 (default) to accept local connections only. Change to 0.0.0.0 only if you need LAN access and understand the security implications. Requires restart.',
            },
            'server:port': {
                'type': 'int', 'default': 8000, 'label': 'Port', 'group': 'server',
                'description': 'TCP port the web server listens on. Default is 8000. Requires restart.',
            },
            # UI
            'ui:language': {
                'type': 'string',
                'default': '',
                'label': 'Interface Language',
                'group': 'ui',
                'description': 'Override the interface language. Empty = use browser default.',
            },
            'ui:theme': {
                'type': 'string',
                'default': '{}',
                'label': 'UI Theme Overrides',
                'group': 'ui',
                'hidden': True,
                'description': 'JSON map of CSS custom property overrides applied to every page.',
            },
        }

    def get(self, key: str):
        if ':' not in key:
            raise ValueError("Setting key must be in the format 'section:key'")
        
        section, option = key.split(':', 1)
        
        # 1. Check database for user-set value (DB may not be ready yet at import time)
        try:
            from core import manager as _manager
            db_value = _manager.get_setting(key)
            if db_value is not None:
                return db_value
        except Exception:
            pass

        # 2. Fallback to config.ini
        if self.config.has_option(section, option):
            return self.config.get(section, option)
            
        # 3. Fallback to schema default
        return self.schema.get(key, {}).get('default')

    def set(self, key: str, value):
        if key not in self.schema:
            raise KeyError(f"'{key}' is not a valid or UI-configurable setting.")
        from core import manager as _manager
        _manager.set_setting(key, value)

    def get_all_configurable(self) -> list[dict]:
        """Returns a list of all UI-configurable settings with their current values."""
        all_settings = []
        for key, meta in self.schema.items():
            if meta.get('hidden', False):
                continue
            all_settings.append({
                'key': key,
                'label': meta['label'],
                'value': self.get(key),
                'type': meta['type'],
                'group': meta.get('group', 'general'),
                'description': meta.get('description', ''),
            })
        return all_settings

class SettingsResolver:
    """
    Vault-aware settings resolver. Lightweight — create per task invocation.
    Resolution chain: vault_settings -> settings.db global -> config.ini -> schema default
    """

    def __init__(self, vault_id: str | None = None, _settings_obj: 'Settings | None' = None):
        self.vault_id = vault_id
        # Use the provided Settings object if given; otherwise fall back to the
        # global singleton (which has config.ini loaded) so the full 4-tier chain is
        # preserved: vault_settings -> settings.db global -> config.ini -> schema default.
        self._s = _settings_obj or settings

    def get(self, key: str):
        if ':' not in key:
            raise ValueError("Setting key must be 'section:key'")

        # Tier 1: vault-specific override
        if self.vault_id:
            try:
                from core.manager import get_settings_db_path, _connect
                with _connect(get_settings_db_path()) as conn:
                    row = conn.execute(
                        "SELECT value FROM vault_settings WHERE vault_id=? AND key=?",
                        (self.vault_id, key)
                    ).fetchone()
                    if row:
                        return row['value']
            except Exception:
                pass

        # Tiers 2-4: delegate to the global Settings object
        return self._s.get(key)


# --- Global Settings Singleton ---
_config_path = os.path.join(os.path.dirname(__file__), '..', 'config.ini')
settings = Settings(config_path=_config_path)

# Export the schema for tests and external introspection
SETTINGS_SCHEMA = settings.schema
