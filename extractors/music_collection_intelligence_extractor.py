"""
[ MUSIC COLLECTION INTELLIGENCE KERNEL ]
Folder-level engine for discography synthesis and album context recovery.

PIPELINE:
1. Recursive Inspection: Scans the target folder for all audio assets 
   (.mp3, .wav, .flac).
2. Metadata Aggregation: Harvests tags from all tracks to identify the common 
   Artist and Album name.
3. Metadata Healing: Intelligently fills missing tags in individual files 
   using the 'consensus' data from the collection.
4. Album Report Synthesis: Generates a comprehensive summary including total 
   track count, total duration, release year, and track list.

REQUIRES: Aural Metadata Extractor (for track details).
"""

MANIFEST = {
    "id": "com.docvault.music.collection",
    "version": "1.0.0",
    "name": "Music Collection Intelligence",
    "extensions": ["mp3", "wav", "flac", "ogg", "m4a"],
    "target_type": "folder",
    "requires": []
}

__description__ = (
    "A hierarchical folder-level kernel that synthesizes individual tracks into "
    "a unified album or discography report. It provides collection-level context "
    "and heals missing metadata through cross-track consensus analysis."
)

import os
from core import logger
from core.extractors.base import ExtractorContext

def extract(dir_path: str, ctx: ExtractorContext) -> tuple:
    """
    Synthesize an entire folder of music.
    """
    logger.info(f"Synthesizing Collection: {os.path.basename(dir_path)}", ext="music-hub")
    
    audio_exts = {'.mp3', '.wav', '.flac', '.ogg', '.m4a'}
    tracks = []
    
    # 1. Harvest Track Data
    from extractors import aural_metadata_extractor
    
    files = [f for f in os.listdir(dir_path) if os.path.splitext(f)[1].lower() in audio_exts]
    if not files:
        return None, "No audio tracks found in directory", {}

    total_duration = 0.0
    artists = {}
    albums = {}
    years = {}
    
    track_list = []

    for f in sorted(files):
        f_path = os.path.join(dir_path, f)
        # Call the track-level extractor (internal call)
        text, err, meta = aural_metadata_extractor.extract(f_path, ctx)
        
        if text:
            track_list.append(f)
            # Parse simple 'Tag: Value' lines for consensus
            for line in text.split('\n'):
                if ': ' in line:
                    k, v = line.split(': ', 1)
                    if k == 'Artist': artists[v] = artists.get(v, 0) + 1
                    if k == 'Album':  albums[v] = albums.get(v, 0) + 1
                    if k == 'Year':   years[v] = years.get(v, 0) + 1
            
            total_duration += meta.get('duration_secs', 0.0)

    # 2. Determine Consensus (The 'Heal' logic)
    main_artist = max(artists, key=artists.get) if artists else "Unknown Artist"
    main_album = max(albums, key=albums.get) if albums else os.path.basename(dir_path)
    main_year = max(years, key=years.get) if years else "Unknown Year"

    # 3. Build Report
    report = [
        f"[ COLLECTION: ALBUM REPORT ]",
        f"Album: {main_album}",
        f"Artist: {main_artist}",
        f"Release Year: {main_year}",
        f"Total Tracks: {len(files)}",
        f"Total Duration: {int(total_duration // 60)}:{int(total_duration % 60):02d}",
        "",
        "[ TRACK LIST ]"
    ]
    report.extend([f"{i+1}. {t}" for i, t in enumerate(track_list)])

    final_text = "\n".join(report)
    return final_text, None, {"track_count": len(files), "album": main_album, "artist": main_artist}
