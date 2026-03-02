from extractors import (
    text_extractor,
    image_extractor,
    word_extractor,
    excel_extractor,
    pptx_extractor,
    plaintext_extractor,
    ocr_extractor,
    transcriber,
    video_extractor,
    metadata_extractor,
    unknown_extractor,
    gdrive_extractor,
)

# Normalise __name__ to the bare module name so callers can use e.__name__
# to identify an extractor without the package prefix.
_all_extractors = [
    text_extractor, image_extractor, word_extractor, excel_extractor,
    pptx_extractor, plaintext_extractor, ocr_extractor, transcriber,
    video_extractor, metadata_extractor, unknown_extractor, gdrive_extractor,
]
for _mod in _all_extractors:
    _mod.__name__ = _mod.__name__.split('.')[-1]

UNKNOWN = unknown_extractor

ROUTES = {
    # Documents
    'pdf':  [text_extractor, image_extractor],
    'docx': [word_extractor],
    'doc':  [word_extractor],
    'xlsx': [excel_extractor],
    'xls':  [excel_extractor],
    'pptx': [pptx_extractor],
    'ppt':  [pptx_extractor],
    # Plain text / code
    'txt':  [plaintext_extractor],
    'md':   [plaintext_extractor],
    'csv':  [plaintext_extractor],
    'json': [plaintext_extractor],
    'py':   [plaintext_extractor],
    'js':   [plaintext_extractor],
    'ts':   [plaintext_extractor],
    'html': [plaintext_extractor],
    'xml':  [plaintext_extractor],
    'yaml': [plaintext_extractor],
    'toml': [plaintext_extractor],
    'log':  [plaintext_extractor],
    # Images
    'jpg':  [ocr_extractor],
    'jpeg': [ocr_extractor],
    'png':  [ocr_extractor],
    'tiff': [ocr_extractor],
    'tif':  [ocr_extractor],
    'bmp':  [ocr_extractor],
    'webp': [ocr_extractor],
    # Audio
    'mp3':  [metadata_extractor, transcriber],
    'wav':  [metadata_extractor, transcriber],
    'm4a':  [metadata_extractor, transcriber],
    'flac': [metadata_extractor, transcriber],
    'ogg':  [metadata_extractor, transcriber],
    # Video
    'mp4':  [metadata_extractor, video_extractor],
    'mov':  [metadata_extractor, video_extractor],
    'mkv':  [metadata_extractor, video_extractor],
    'avi':  [metadata_extractor, video_extractor],
    'webm': [metadata_extractor, video_extractor],
    # Google Drive stubs
    'gdoc':    [gdrive_extractor],
    'gsheet':  [gdrive_extractor],
    'gslides': [gdrive_extractor],
    'gform':   [gdrive_extractor],
    'gdraw':   [gdrive_extractor],
}

# Higher value = higher priority
PRIORITIES = {
    # Fast text-based formats
    'txt': 20, 'md': 20, 'csv': 20, 'json': 20, 'py': 20, 'js': 20, 'ts': 20,
    'html': 20, 'xml': 20, 'yaml': 20, 'toml': 20, 'log': 20,
    # Standard documents & images (default priority)
    'pdf': 10, 'docx': 10, 'doc': 10, 'xlsx': 10, 'xls': 10,
    'pptx': 10, 'ppt': 10, 'jpg': 10, 'jpeg': 10, 'png': 10,
    'tiff': 10, 'tif': 10, 'bmp': 10, 'webp': 10,
    # Slow media formats
    'mp3': 5, 'wav': 5, 'm4a': 5, 'flac': 5, 'ogg': 5,
    'mp4': 5, 'mov': 5, 'mkv': 5, 'avi': 5, 'webm': 5,
}
DEFAULT_PRIORITY = 10


def get_extractors(file_type: str) -> list:
    """Return the ordered list of extractors for a given file extension."""
    return ROUTES.get(file_type.lower(), [UNKNOWN])

def get_priority(file_type: str) -> int:
    """Return the priority for a given file extension."""
    return PRIORITIES.get(file_type.lower(), DEFAULT_PRIORITY)
