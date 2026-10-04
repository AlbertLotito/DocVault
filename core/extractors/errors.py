"""Helpers for turning raw tool output into useful stored error messages."""
import re

_BANNER_START = re.compile(r'\b(ffmpeg|ffprobe) version\b')
_BANNER_LINE = re.compile(r'^\s+(built with|configuration:|lib\w+\s+\d)')


def summarize_ffmpeg_error(text: str, max_lines: int = 5) -> str:
    """Strip ffmpeg/ffprobe's version banner and keep the last few real lines.

    ffmpeg prints a ~25-line banner (version, build config, library versions)
    before the actual error, so storing the start of stderr kept only the
    banner. Any prefix before the banner (e.g. whisper's "Failed to load
    audio: ") is preserved. Text without a banner is returned unchanged.
    """
    text = (text or '').strip()
    m = _BANNER_START.search(text)
    if not m:
        return text
    prefix = text[:m.start()]
    lines = text[m.start():].splitlines()[1:]          # drop the "ffmpeg version …" line
    real = [l.strip() for l in lines if l.strip() and not _BANNER_LINE.match(l)]
    if not real:
        return prefix + 'ffmpeg failed without an error message (only its version banner was printed)'
    return prefix + '\n'.join(real[-max_lines:])
