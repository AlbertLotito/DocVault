import os
import tempfile
import ffmpeg
from extractors import transcriber


def _extract_audio(video_path: str) -> tuple:
    """Extract audio from video to a temporary WAV file."""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp.close()
        (
            ffmpeg
            .input(video_path)
            .output(tmp.name, acodec='pcm_s16le', ac=1, ar='16000', y=None)
            .run(quiet=True, overwrite_output=True)
        )
        return tmp.name, None
    except ffmpeg.Error as e:
        return None, f"Audio extraction failed: {e.stderr.decode('utf8', errors='replace')}"
    except Exception as e:
        return None, f"Unexpected error extracting audio: {e}"


def extract(file_path: str) -> tuple:
    """
    Extracts audio from a video file and transcribes it via Whisper.
    """
    print(f"  [video] Processing: {os.path.basename(file_path)}")
    audio_path, err = _extract_audio(file_path)
    if err:
        return None, err
    try:
        text, err = transcriber.extract(audio_path)
        return text, err
    finally:
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)
