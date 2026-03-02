import configparser
import os
from core import manager

class Settings:
    def __init__(self, config_path):
        self.config = configparser.ConfigParser()
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
                'type': 'string', 'default': 'deepseek-r1:14b', 'label': 'Chat Model', 'group': 'ollama',
                'description': 'Ollama model used for RAG answers in the "Ask" chat. Must be pulled first with "ollama pull <model>". Recommended: deepseek-r1:14b (speed/quality balance) or llama3.1:70b for higher quality.',
            },
            'ollama:embed_model': {
                'type': 'string', 'default': 'nomic-embed-text', 'label': 'Embedding Model', 'group': 'ollama',
                'description': 'Ollama model used to generate vector embeddings for semantic search. Changing this invalidates all existing embeddings — you must delete the Qdrant collection and re-embed everything. Default: nomic-embed-text.',
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
            # Qdrant
            'qdrant:host': {
                'type': 'string', 'default': 'localhost', 'label': 'Host', 'group': 'qdrant',
                'description': 'Hostname or IP address of the Qdrant vector database. Usually "localhost" when Qdrant runs in Docker on the same machine.',
            },
            'qdrant:port': {
                'type': 'int', 'default': 6333, 'label': 'Port', 'group': 'qdrant',
                'description': 'Port Qdrant listens on. Default is 6333. Change only if you have configured Qdrant to use a different port.',
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
            # Vision
            'vision:model': {
                'type': 'string', 'default': 'minicpm-v', 'label': 'Vision Model', 'group': 'vision',
                'description': 'Ollama vision model used to describe standalone images and transcribe scanned PDF pages. Must support image input. Pull it first: "ollama pull minicpm-v". Alternatives: llava, llava-llama3.',
            },
            'vision:describe_images': {
                'type': 'string', 'default': 'true', 'label': 'Describe images', 'group': 'vision',
                'description': 'Set to "true" to enable vision-based description for image files and images embedded in PDFs. Set to "false" to disable — Tesseract OCR will still run as a fallback for standalone images. Disable to speed up extraction if you do not need image understanding.',
            },
            # Embeddings
            'embeddings:chunk_size': {
                'type': 'int', 'default': 600, 'label': 'Chunk size (chars)', 'group': 'embeddings',
                'description': 'Maximum number of characters per text chunk. Smaller chunks produce more precise semantic matches but generate more Qdrant vectors. Recommended range: 400–800. Changing this requires deleting the Qdrant collection and re-embedding all documents.',
            },
            'embeddings:chunk_overlap': {
                'type': 'int', 'default': 100, 'label': 'Chunk overlap (chars)', 'group': 'embeddings',
                'description': 'Number of characters shared between adjacent chunks. Overlap prevents context from being lost at chunk boundaries (e.g. a sentence split across two chunks). Recommended: 10–20% of chunk size. Changing this requires re-embedding.',
            },
            'embeddings:score_threshold': {
                'type': 'float', 'default': 0.65, 'label': 'Search score threshold', 'group': 'embeddings',
                'description': 'Minimum cosine similarity score (0.0–1.0) for a chunk to appear in search results on the Search page. Higher values return only strong matches and reduce noise. Does not affect the RAG chat threshold (see Search tab).',
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
            # Google Drive
            'google:credentials_path': {
                'type': 'string', 'default': '', 'label': 'Credentials JSON path', 'group': 'google',
                'description': 'Path to the OAuth 2.0 client credentials JSON file downloaded from Google Cloud Console. Required to access Google Drive files (.gdoc, .gsheet, .gslides). Download from: APIs & Services → Credentials → your Desktop app → Download JSON.',
            },
            'google:token_path': {
                'type': 'string', 'default': '', 'label': 'Token JSON path', 'group': 'google',
                'description': 'Path where the OAuth access token will be saved after the first authorisation. This file is created automatically when you authorise DocVault via the Utilities page. Keep it in the credentials folder alongside the credentials JSON.',
            },
        }

    def get(self, key: str):
        if ':' not in key:
            raise ValueError("Setting key must be in the format 'section:key'")
        
        section, option = key.split(':', 1)
        
        # 1. Check database for user-set value (DB may not be ready yet at import time)
        try:
            db_value = manager.get_setting(key)
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
        manager.set_setting(key, value)

    def get_all_configurable(self) -> list[dict]:
        """Returns a list of all UI-configurable settings with their current values."""
        all_settings = []
        for key, meta in self.schema.items():
            all_settings.append({
                'key': key,
                'label': meta['label'],
                'value': self.get(key),
                'type': meta['type'],
                'group': meta.get('group', 'general'),
                'description': meta.get('description', ''),
            })
        return all_settings

# --- Global Settings Singleton ---
_config_path = os.path.join(os.path.dirname(__file__), '..', 'config.ini')
settings = Settings(config_path=_config_path)
