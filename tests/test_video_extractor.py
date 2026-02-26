import sys
from unittest.mock import patch, MagicMock

# Prevent transcriber.py from actually loading Whisper at import time.
# transcriber.py calls _load_model() at module level, which calls whisper.load_model().
# We patch whisper and torch before either module is imported.
_mock_whisper = MagicMock()
_mock_torch = MagicMock()
_mock_torch.cuda.is_available.return_value = False
sys.modules.setdefault('whisper', _mock_whisper)
sys.modules.setdefault('torch', _mock_torch)

from extractors import video_extractor


@patch('extractors.video_extractor.transcriber.extract')
@patch('extractors.video_extractor._extract_audio')
def test_extract_transcribes_audio(mock_audio, mock_transcribe):
    mock_audio.return_value = ('/tmp/audio.wav', None)
    mock_transcribe.return_value = ('Video transcript text.', None)

    text, error = video_extractor.extract('fake.mp4')
    assert error is None
    assert text == 'Video transcript text.'
    mock_audio.assert_called_once_with('fake.mp4')


@patch('extractors.video_extractor._extract_audio')
def test_extract_returns_error_on_audio_failure(mock_audio):
    mock_audio.return_value = (None, 'ffmpeg error')

    text, error = video_extractor.extract('fake.mp4')
    assert text is None
    assert 'ffmpeg error' in error


@patch('extractors.video_extractor.transcriber.extract')
@patch('extractors.video_extractor._extract_audio')
def test_extract_returns_error_on_transcription_failure(mock_audio, mock_transcribe):
    mock_audio.return_value = ('/tmp/audio.wav', None)
    mock_transcribe.return_value = (None, 'Whisper failed')

    text, error = video_extractor.extract('fake.mp4')
    assert text is None
    assert 'Whisper failed' in error
