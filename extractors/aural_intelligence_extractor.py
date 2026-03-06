"""
[ AURAL INTELLIGENCE KERNEL ]
The system's primary speech-to-text engine for high-fidelity audio transcription.

PIPELINE:
1. Hardware Optimization (PyTorch): Automatically detects and utilizes NVIDIA 
   CUDA GPUs for high-speed inference. Uses FP16 precision for maximum throughput.
2. Whisper Large-v3 (OpenAI): Performs state-of-the-art transcription, 
   automatically handling language detection, punctuation, and noisy environments.
3. Memory Persistence: Keeps the model resident in VRAM/RAM for zero-latency 
   subsequent processing.

REQUIRES: NVIDIA GPU (8GB+ VRAM recommended), PyTorch, OpenAI-Whisper.
"""

MANIFEST = {
    "id": "com.openai.whisper.large-v3",
    "version": "1.0.0",
    "name": "Aural Intelligence Engine",
    "extensions": ["mp3", "wav", "m4a", "flac", "ogg"],
    "requires": ["torch", "openai-whisper"]
}

__description__ = (
    "A state-of-the-art 'Aural Intelligence' kernel utilizing OpenAI's Whisper "
    "large-v3 model. It performs high-fidelity speech-to-text conversion with "
    "automatic language detection and punctuation restoration, optimized for "
    "NVIDIA hardware acceleration."
)

import os
import torch
import whisper
from core import logger
from core.extractors.base import ExtractorContext


def _load_model() -> whisper.Whisper | None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading Whisper large-v3 model on {device}...", ext="aural-ai")
    try:
        model = whisper.load_model("large-v3", device=device)
        logger.info("Whisper model loaded successfully.", ext="aural-ai")
        return model
    except Exception as e:
        logger.critical(f"Could not load Whisper model: {e}", ext="aural-ai")
        return None


WHISPER_MODEL = _load_model()


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Transcribes audio from a media file using Whisper large-v3.

    Returns:
        (str, None) on success.
        (None, str) on failure.
    """
    logger.info(f"Transcribing: {os.path.basename(file_path)}", ext="aural-ai")
    if not WHISPER_MODEL:
        return None, "Aural Intelligence model is not available."
    try:
        use_fp16 = torch.cuda.is_available()
        result = WHISPER_MODEL.transcribe(file_path, fp16=use_fp16)
        return result['text'], None
    except Exception as e:
        return None, f"Transcription failed: {e}"
