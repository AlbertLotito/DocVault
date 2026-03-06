"""
[ MEDIA TECHNICAL DIAGNOSTICS KERNEL ]
Surgical bitstream inspection engine for audio and video media files.

PIPELINE:
1. Bitstream Probing (ffprobe): Utilizes the FFmpeg suite to inspect file 
   headers without full stream decoding. 
2. Stream Discovery: Identifies technical specifications including resolution, 
   framerate, codecs, duration, sample rates, and bit depth.
3. Schema Normalization: Transforms raw ffprobe JSON output into structured 
   technical metadata for the system registry.

INTEGRATION:
Provides the baseline technical context required by high-intelligence media 
kernels (Aural and Video) and populates the system's technical info panels.

REQUIRES: FFmpeg (specifically the ffprobe binary).
"""

__description__ = (
    "A high-precision media analysis engine powered by ffprobe. It performs "
    "surgical bitstream inspection to extract technical specifications, resolution, "
    "codecs, and duration for all audio and video formats, providing the "
    "baseline context for the media intelligence pipeline."
)

import os
import ffmpeg
from core import logger


def extract(file_path: str) -> tuple:
    """
    Extracts technical metadata from a media file using ffprobe.

    Returns:
        (dict, None) on success.
        (None, str) on failure.
    """
    logger.info(f"Probing: {os.path.basename(file_path)}", ext="metadata-ai")
    try:
        metadata = ffmpeg.probe(file_path)
        return metadata, None
    except ffmpeg.Error as e:
        return None, f"ffprobe failed: {e.stderr.decode('utf8', errors='replace')}"
    except Exception as e:
        return None, f"Unexpected metadata error: {e}"
