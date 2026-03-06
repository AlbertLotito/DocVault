from extractors import (
    text_extractor,
    image_extractor,
    microsoft_word_extractor,
    microsoft_excel_extractor,
    microsoft_powerpoint_extractor,
    plaintext_extractor,
    intelligent_image_extractor,
    aural_intelligence_extractor,
    multimodal_video_intelligence_extractor,
    media_technical_diagnostics_extractor,
    fallback_kernel,
    gdrive_extractor,
)

# Normalise __name__ to the bare module name so callers can use e.__name__
# to identify an extractor without the package prefix.
_all_extractors = [
    text_extractor, image_extractor, microsoft_word_extractor, 
    microsoft_excel_extractor, microsoft_powerpoint_extractor, 
    plaintext_extractor, intelligent_image_extractor, 
    aural_intelligence_extractor, multimodal_video_intelligence_extractor, 
    media_technical_diagnostics_extractor, fallback_kernel, gdrive_extractor,
]
for _mod in _all_extractors:
    _mod.__name__ = _mod.__name__.split('.')[-1]

UNKNOWN = fallback_kernel

ROUTES = {
    # Documents
    'pdf':  [text_extractor, image_extractor],
    'docx': [microsoft_word_extractor],
    'doc':  [microsoft_word_extractor],
    'xlsx': [microsoft_excel_extractor],
    'xls':  [microsoft_excel_extractor],
    'pptx': [microsoft_powerpoint_extractor],
    'ppt':  [microsoft_powerpoint_extractor],

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
    'jpg':  [intelligent_image_extractor],
    'jpeg': [intelligent_image_extractor],
    'png':  [intelligent_image_extractor],
    'tiff': [intelligent_image_extractor],
    'tif':  [intelligent_image_extractor],
    'bmp':  [intelligent_image_extractor],
    'webp': [intelligent_image_extractor],
    # Audio
    'mp3':  [media_technical_diagnostics_extractor, aural_intelligence_extractor],
    'wav':  [media_technical_diagnostics_extractor, aural_intelligence_extractor],
    'm4a':  [media_technical_diagnostics_extractor, aural_intelligence_extractor],
    'flac': [media_technical_diagnostics_extractor, aural_intelligence_extractor],
    'ogg':  [media_technical_diagnostics_extractor, aural_intelligence_extractor],
    # Video
    'mp4':  [media_technical_diagnostics_extractor, multimodal_video_intelligence_extractor],
    'mov':  [media_technical_diagnostics_extractor, multimodal_video_intelligence_extractor],
    'mkv':  [media_technical_diagnostics_extractor, multimodal_video_intelligence_extractor],
    'avi':  [media_technical_diagnostics_extractor, multimodal_video_intelligence_extractor],
    'webm': [media_technical_diagnostics_extractor, multimodal_video_intelligence_extractor],
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


def get_extractors(file_type: str, vault_id: str = None) -> list:
    """Return the ordered list of extractors for a given file extension."""
    if vault_id:
        from core.vault_manager import VaultManager
        vm = VaultManager()
        config = vm.get_vault_extractors(vault_id)
        if config:
            # Filter all_extractors based on the vault's custom list of enabled extractors
            # ordered by the vault's custom priority.
            enabled = [c['name'] for c in config if c.get('enabled', True)]
            if enabled:
                # Find the actual modules for these names
                custom_stack = []
                for name in enabled:
                    for mod in _all_extractors:
                        if getattr(mod, '__name__', '') == name:
                            custom_stack.append(mod)
                            break
                # Only use custom stack if it actually contains extractors for this file type
                # (Safety: we don't want to run a video extractor on a PDF just because it's enabled)
                default_stack = ROUTES.get(file_type.lower(), [UNKNOWN])
                return [e for e in custom_stack if e in default_stack]

    return ROUTES.get(file_type.lower(), [UNKNOWN])

def get_priority(file_type: str, vault_id: str = None) -> int:
    """Return the priority for a given file extension."""
    # Note: Currently vault-level priority is handled at the task scheduling layer, 
    # not at the file-type layer. Global PRIORITIES still apply for file type.
    return PRIORITIES.get(file_type.lower(), DEFAULT_PRIORITY)
