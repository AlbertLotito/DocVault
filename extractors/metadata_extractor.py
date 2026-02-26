import os
import ffmpeg


def extract(file_path: str) -> tuple:
    """
    Extracts technical metadata from a media file using ffprobe.

    Returns:
        (dict, None) on success.
        (None, str) on failure.
    """
    print(f"  [metadata] Probing: {os.path.basename(file_path)}")
    try:
        metadata = ffmpeg.probe(file_path)
        return metadata, None
    except ffmpeg.Error as e:
        return None, f"ffprobe failed: {e.stderr.decode('utf8', errors='replace')}"
    except Exception as e:
        return None, f"Unexpected metadata error: {e}"
