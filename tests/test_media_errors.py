"""
ffmpeg/ffprobe print a ~25-line version banner before the actual error, so the
596 stored media failures held only the banner and the real error was cut off.
summarize_ffmpeg_error() keeps what matters.
"""
from core.extractors.errors import summarize_ffmpeg_error

BANNER = """ffmpeg version 9.0.1-essentials_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers
  built with gcc 16.1.0 (Rev2, Built by MSYS2 project)
  configuration: --enable-gpl --enable-version3 --enable-static --disable-w32threads
  libavutil      61.  1.101 / 61.  1.101
  libavcodec     63.  1.101 / 63.  1.101
  libavformat    63.  1.101 / 63.  1.101
  libswscale     10.  1.101 / 10.  1.101
"""


def test_keeps_the_real_error_after_the_banner():
    stderr = BANNER + "[mp3 @ 0000021] Header missing\nZ:\\Audio\\track.mp3: Invalid data found when processing input\n"
    msg = summarize_ffmpeg_error(stderr)
    assert 'Invalid data found when processing input' in msg
    assert 'Header missing' in msg
    assert 'configuration' not in msg and 'libavcodec' not in msg and 'built with' not in msg


def test_banner_only_output_says_so():
    msg = summarize_ffmpeg_error(BANNER)
    assert msg == 'ffmpeg failed without an error message (only its version banner was printed)'


def test_keeps_only_the_last_few_lines():
    stderr = BANNER + ''.join(f'warning line {i}\n' for i in range(50)) + 'Conversion failed!\n'
    msg = summarize_ffmpeg_error(stderr, max_lines=3)
    assert msg.splitlines() == ['warning line 48', 'warning line 49', 'Conversion failed!']


def test_plain_message_without_banner_is_unchanged():
    assert summarize_ffmpeg_error('No such file or directory') == 'No such file or directory'


def test_whisper_style_wrapped_message():
    """whisper raises RuntimeError(f"Failed to load audio: {stderr}")."""
    stderr = 'Failed to load audio: ' + BANNER + 'x.opus: Permission denied\n'
    msg = summarize_ffmpeg_error(stderr)
    assert msg == 'Failed to load audio: x.opus: Permission denied'
