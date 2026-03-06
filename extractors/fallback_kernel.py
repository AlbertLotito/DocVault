"""
[ SYSTEM FALLBACK KERNEL ]
The final safety tier in the extraction pipeline. This kernel is invoked 
only when no specialized extractor is registered for a given file extension.
"""

MANIFEST = {
    "id": "com.docvault.system.fallback",
    "version": "1.0.0",
    "name": "System Fallback Kernel",
    "extensions": ["*"], # Special wildcard for fallback
    "requires": []
}

__description__ = (
    "The system's terminal fallback kernel. It handles unregistered or binary "
    "file types by identifying the extension and logging a 'unsupported format' "
    "record. This ensures the ingestion pipeline remains robust."
)

import os
from core import logger
from core.extractors.base import ExtractorContext

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Catch-all for unregistered types.
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').upper() or "NO_EXT"
    logger.warn(f"No kernel registered for .{ext} files. Flagging as unsupported.", ext="fallback")
    return None, f"Unsupported format: {ext}"
