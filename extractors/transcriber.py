import os
import torch
import whisper


def _load_model():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading Whisper large-v3 model on {device}...")
    try:
        model = whisper.load_model("large-v3", device=device)
        print("Whisper model loaded successfully.")
        return model
    except Exception as e:
        print(f"FATAL: Could not load Whisper model: {e}")
        return None


WHISPER_MODEL = _load_model()


def extract(file_path: str) -> tuple:
    """
    Transcribes audio from a media file using Whisper large-v3.

    Returns:
        (str, None) on success.
        (None, str) on failure.
    """
    print(f"  [transcribe] Transcribing: {os.path.basename(file_path)}")
    if not WHISPER_MODEL:
        return None, "Whisper model is not available."
    try:
        use_fp16 = torch.cuda.is_available()
        result = WHISPER_MODEL.transcribe(file_path, fp16=use_fp16)
        return result['text'], None
    except Exception as e:
        return None, f"Failed to transcribe: {e}"
