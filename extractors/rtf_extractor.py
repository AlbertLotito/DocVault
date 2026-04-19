"""
[ RTF EXTRACTION KERNEL ]
Extracts plain text from Rich Text Format (.rtf) files.

PIPELINE:
1. Decode: attempts UTF-8 then Latin-1 (RTF files are commonly Latin-1 encoded)
2. Strip: removes all RTF control codes using striprtf
3. Normalize: collapses excessive blank lines

REQUIRES: striprtf (pip install striprtf)
"""

MANIFEST = {
    "id": "com.docvault.document.rtf",
    "version": "1.0.0",
    "name": "RTF Text Extractor",
    "extensions": ["rtf"],
    "requires": ["striprtf"]
}

__description__ = (
    "Extracts plain text from Rich Text Format (.rtf) files by stripping RTF "
    "control codes. Handles encoding variations common in legacy RTF documents."
)

import os
import re
from core import logger
from core.extractors.base import ExtractorContext


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Reading RTF: {os.path.basename(file_path)}", ext="rtf")
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError:
        return None, "striprtf not installed (pip install striprtf)"

    for encoding in ('utf-8', 'latin-1', 'cp1252'):
        try:
            with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                raw = f.read()
            break
        except (OSError, FileNotFoundError) as e:
            return None, f"Could not read file: {e}"

    try:
        text = rtf_to_text(raw)
    except Exception as e:
        return None, f"RTF parsing failed: {e}"

    # Collapse runs of 3+ blank lines down to 2
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return None, "No text content extracted from RTF"

    return text, None
