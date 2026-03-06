"""
[ SYSTEM FALLBACK KERNEL ]
The final safety tier in the extraction pipeline. This kernel is invoked 
only when no specialized extractor is registered for a given file extension.
"""

__description__ = (
    "The system's terminal fallback kernel. It handles unregistered or binary "
    "file types by identifying the extension and logging a 'unsupported format' "
    "record. This ensures the ingestion pipeline remains robust and provides "
    "clear feedback on which file types require new kernel development."
)

import os
from core import logger

def extract(file_path: str) -> tuple:
    """
    Catch-all for unregistered types.
    """
    ext = os.path.splitext(file_path)[1].lstrip('.').upper() or "NO_EXT"
    logger.warn(f"No kernel registered for .{ext} files. Flagging as unsupported.", ext="fallback")
    return None, f"Unsupported format: {ext}"
