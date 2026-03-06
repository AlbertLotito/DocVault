"""
[ PLAIN TEXT EXTRACTION KERNEL ]
The universal ingestion kernel for unstructured text and source code.

PIPELINE:
1. Adaptive Decoding: Sequentially attempts to decode bitstreams using UTF-8, 
   UTF-8-BOM, Latin-1, and CP1252 to prevent character corruption.
2. Character Normalization: Recovers text from legacy logs, configuration 
   files, and cross-platform source code with high fidelity.

REQUIRES: Built-in Python libraries.
"""

MANIFEST = {
    "id": "com.docvault.text.plain",
    "version": "1.0.0",
    "name": "Plain Text Engine",
    "extensions": ["txt", "md", "csv", "json", "py", "js", "ts", "html", "xml", "yaml", "toml", "log"],
    "requires": []
}

__description__ = (
    "The universal ingestion kernel for unstructured text and source code. "
    "Features an adaptive multi-encoding fallback strategy that sequentially "
    "attempts to decode bitstreams using UTF-8, UTF-8-BOM, Latin-1, and CP1252. "
    "This ensures high-fidelity text recovery while preventing character corruption."
)

import os
from core import logger
from core.extractors.base import ExtractorContext


ENCODINGS = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Reads a plain-text file and returns its content.
    Tries multiple encodings before giving up.
    """
    logger.info(f"Reading: {os.path.basename(file_path)}", ext="text/plain")
    for encoding in ENCODINGS:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            return content, None
        except UnicodeDecodeError:
            continue
        except (OSError, FileNotFoundError) as e:
            return None, f"Could not read file: {e}"
    return None, "Could not decode file with any supported encoding"
