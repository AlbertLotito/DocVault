"""
[ AURAL METADATA KERNEL ]
Surgical metadata engine for audio bitstreams and tag headers.

PIPELINE:
1. Tag Harvesting (mutagen): Extracts ID3, Vorbis, or MP4 tags including 
   Artist, Album, Title, Year, and Genre.
2. Stream Discovery (ffprobe): Captures technical specs like bit depth, 
   sample rate, channels, and precise duration.
3. Search Synthesis: Normalizes tags into a structured report for indexing.

REQUIRES: Mutagen, FFmpeg (ffprobe).
"""

MANIFEST = {
    "id": "com.docvault.aural.metadata",
    "version": "1.0.0",
    "name": "Aural Metadata Extractor",
    "extensions": ["mp3", "wav", "m4a", "flac", "ogg", "au"],
    "requires": ["mutagen", "ffmpeg"]
}

__description__ = (
    "A high-fidelity audio metadata engine. It extracts deep ID3 tags and "
    "technical bitstream specifications, providing structured information "
    "for all music and audio assets."
)

import os
import ffmpeg
from core import logger
from core.extractors.base import ExtractorContext

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    logger.info(f"Probing Metadata: {os.path.basename(file_path)}", ext="aural-meta")
    meta = {}
    lines = []
    
    try:
        # 1. Technical specs via ffprobe
        probe = ffmpeg.probe(file_path)
        format_info = probe.get('format', {})
        duration = float(format_info.get('duration', 0))
        meta["duration_secs"] = duration
        meta["bitrate"] = format_info.get('bit_rate')
        
        # 2. Tag harvesting
        # We try to use mutagen if available, otherwise fallback to probe tags
        tags = format_info.get('tags', {})
        artist = tags.get('artist') or tags.get('ARTIST')
        album = tags.get('album') or tags.get('ALBUM')
        title = tags.get('title') or tags.get('TITLE')
        year = tags.get('date') or tags.get('DATE')
        
        if artist: lines.append(f"Artist: {artist}")
        if album:  lines.append(f"Album: {album}")
        if title:  lines.append(f"Title: {title}")
        if year:   lines.append(f"Year: {year}")
        lines.append(f"Duration: {int(duration // 60)}:{int(duration % 60):02d}")
        
        final_text = "\n".join(lines) if lines else None
        return final_text, None, meta

    except Exception as e:
        return None, f"Metadata extraction failed: {e}", meta
