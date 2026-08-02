import threading
from unittest.mock import patch, MagicMock

from extractors import multimodal_video_intelligence_extractor as video_extractor


def _ctx():
    from core.extractors.base import ExtractorContext, ExtractorLogger
    return ExtractorContext(
        vault_id='test', file_hash='test',
        cancel_token=threading.Event(),
        logger=MagicMock(spec=ExtractorLogger),
        settings=MagicMock(),
    )


@patch('extractors.multimodal_video_intelligence_extractor.vision_enabled', return_value=False)
@patch('extractors.multimodal_video_intelligence_extractor.aural_intelligence_extractor.extract')
@patch('extractors.multimodal_video_intelligence_extractor._extract_audio')
def test_extract_transcribes_audio(mock_audio, mock_transcribe, mock_vision_enabled):
    mock_audio.return_value = ('/tmp/audio.wav', None)
    mock_transcribe.return_value = ('Video transcript text.', None)

    text, error = video_extractor.extract('fake.mp4', _ctx())
    assert error is None
    assert text == '[Audio Transcript]\nVideo transcript text.'
    mock_audio.assert_called_once_with('fake.mp4')


@patch('extractors.multimodal_video_intelligence_extractor.vision_enabled', return_value=False)
@patch('extractors.multimodal_video_intelligence_extractor._extract_audio')
def test_extract_returns_error_on_audio_failure(mock_audio, mock_vision_enabled):
    mock_audio.return_value = (None, 'ffmpeg error')

    text, error = video_extractor.extract('fake.mp4', _ctx())
    assert text is None
    assert 'ffmpeg error' in error


@patch('extractors.multimodal_video_intelligence_extractor.vision_enabled', return_value=False)
@patch('extractors.multimodal_video_intelligence_extractor.aural_intelligence_extractor.extract')
@patch('extractors.multimodal_video_intelligence_extractor._extract_audio')
def test_extract_returns_error_on_transcription_failure(mock_audio, mock_transcribe, mock_vision_enabled):
    mock_audio.return_value = ('/tmp/audio.wav', None)
    mock_transcribe.return_value = (None, 'Whisper failed')

    text, error = video_extractor.extract('fake.mp4', _ctx())
    assert text is None
    assert 'Whisper failed' in error
