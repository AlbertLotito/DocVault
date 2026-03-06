"""
[ MULTIMODAL VIDEO INTELLIGENCE KERNEL ]
The system's most complex media engine, coordinating audio and visual 
intelligence streams into a unified structured report.

PIPELINE:
1. Stream Splitting (FFmpeg): Isolates the audio stream and harvests high-resolution 
   visual frames at configurable intervals.
2. Aural Analysis (Whisper): Passes the audio stream to the Aural Intelligence 
   kernel for full, punctuated speech-to-text transcription.
3. Visual Scene Analysis (Vision LLM): Iterates through harvested frames, 
   using Vision AI to describe on-screen content (people, objects, slides, or code).
4. Temporal Reconstruction: Synchronizes audio and visual streams using 
   timestamps (e.g., [Frame @ 2:15]) to provide chronological context.

REQUIRES: FFmpeg, Aural Intelligence kernel, Vision AI model.
"""

MANIFEST = {
    "id": "com.docvault.video.multimodal",
    "version": "1.0.0",
    "name": "Multimodal Video Intelligence",
    "extensions": ["mp4", "mov", "mkv", "avi", "webm"],
    "requires": ["ffmpeg", "aural_intelligence_extractor", "ollama"]
}

__description__ = (
    "A sophisticated multimodal engine that synthesizes aural and visual data. "
    "It provides a synchronized report containing a full audio transcript and "
    "timestamped scene descriptions, enabling deep searchability of video content."
)

import os
import shutil
import tempfile
import ffmpeg
from PIL import Image
from extractors import aural_intelligence_extractor
from extractors.vision import describe as vision_describe, is_enabled as vision_enabled
from core.settings import settings
from core import logger
from core.extractors.base import ExtractorContext


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


def _extract_frames(video_path: str, interval: int) -> tuple:
    """
    Extract one frame every `interval` seconds to a temp directory.
    Returns (frames_dir, [(timestamp_secs, filepath), ...], error_str).
    On failure returns (None, [], error_str).
    """
    frames_dir = tempfile.mkdtemp(prefix='docvault_frames_')
    try:
        (
            ffmpeg
            .input(video_path)
            .filter('fps', fps=f'1/{interval}')
            .output(
                os.path.join(frames_dir, 'frame_%05d.png'),
                start_number=0,
            )
            .run(quiet=True, overwrite_output=True)
        )
        frame_files = sorted(f for f in os.listdir(frames_dir) if f.endswith('.png'))
        frames = [(i * interval, os.path.join(frames_dir, f))
                  for i, f in enumerate(frame_files)]
        return frames_dir, frames, None
    except Exception as e:
        shutil.rmtree(frames_dir, ignore_errors=True)
        return None, [], f"Frame extraction failed: {e}"


def _fmt_ts(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Multimodal extraction: Audio Transcript + Visual Scene Descriptions.
    """
    logger.info(f"Processing: {os.path.basename(file_path)}", ext="video-ai")

    parts = []
    errors = []

    # ── Audio transcription ───────────────────────────────────────────────
    audio_path, err = _extract_audio(file_path)
    if err:
        errors.append(err)
    else:
        try:
            # We call the kernel directly as it is part of the system suite
            transcript, err = aural_intelligence_extractor.extract(audio_path, ctx)
            if err:
                errors.append(f"Transcription: {err}")
            elif transcript:
                parts.append(f"[Audio Transcript]\n{transcript}")
                logger.info(f"Transcript: {len(transcript)} chars", ext="video-ai")
        finally:
            if audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)

    # ── Frame descriptions ────────────────────────────────────────────────
    describe_frames = str(settings.get('video:describe_frames') or 'true').lower() \
                      not in ('0', 'false', 'no')

    if describe_frames and vision_enabled():
        interval = int(settings.get('video:frame_interval') or 10)
        frames_dir, frames, err = _extract_frames(file_path, interval)

        if err:
            errors.append(err)
        else:
            logger.info(f"Describing {len(frames)} frames (1 per {interval}s)…", ext="video-ai")
            frame_descriptions = []
            for ts, frame_path in frames:
                try:
                    img = Image.open(frame_path)
                    desc = vision_describe(
                        img,
                        prompt=(
                            'Describe what is visible in this video frame. '
                            'Include any visible text, people, objects, actions, '
                            'or on-screen content such as slides or code.'
                        ),
                    )
                    if desc:
                        frame_descriptions.append(f"[Frame @ {_fmt_ts(ts)}]\n{desc}")
                        logger.debug(f"{_fmt_ts(ts)}: {len(desc)} chars", ext="video-ai")
                except Exception as e:
                    logger.debug(f"{_fmt_ts(ts)}: skipped ({e})", ext="video-ai")

            if frame_descriptions:
                parts.append("[Visual Content]\n" + "\n\n".join(frame_descriptions))

            shutil.rmtree(frames_dir, ignore_errors=True)

    # ── Combine ───────────────────────────────────────────────────────────
    if not parts:
        return None, ' | '.join(errors) if errors else 'No content extracted'

    return "\n\n".join(parts), ' | '.join(errors) if errors else None
