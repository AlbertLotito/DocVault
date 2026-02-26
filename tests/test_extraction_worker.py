import pytest
from unittest.mock import patch, MagicMock
from workers import extraction_worker


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.manager.insert_extracted_image')
@patch('workers.extraction_worker.router.get_extractors')
def test_process_task_success(mock_router, mock_insert_img, mock_complete):
    mock_extractor = MagicMock()
    mock_extractor.__name__ = 'text_extractor'
    mock_extractor.extract.return_value = ('Extracted text', None)
    mock_router.return_value = [mock_extractor]

    task = {'file_hash': 'abc', 'file_path': '/docs/test.pdf', 'file_type': 'pdf'}
    extraction_worker.process_task('test.db', task)

    mock_complete.assert_called_once()
    call_kwargs = mock_complete.call_args[1]
    assert call_kwargs['status'] == 'EXTRACTED'
    assert call_kwargs['text'] == 'Extracted text'


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_process_task_error(mock_router, mock_complete):
    mock_extractor = MagicMock()
    mock_extractor.__name__ = 'text_extractor'
    mock_extractor.extract.return_value = (None, 'File not found')
    mock_router.return_value = [mock_extractor]

    task = {'file_hash': 'abc', 'file_path': '/docs/missing.pdf', 'file_type': 'pdf'}
    extraction_worker.process_task('test.db', task)

    call_kwargs = mock_complete.call_args[1]
    assert call_kwargs['status'] == 'ERROR'
    assert 'File not found' in call_kwargs['error']


@patch('workers.extraction_worker.manager.complete_extraction')
@patch('workers.extraction_worker.router.get_extractors')
def test_unknown_file_type_flagged(mock_router, mock_complete):
    from extractors import unknown_extractor
    mock_router.return_value = [unknown_extractor]

    task = {'file_hash': 'abc', 'file_path': '/docs/weird.xyz', 'file_type': 'xyz'}
    extraction_worker.process_task('test.db', task)

    call_kwargs = mock_complete.call_args[1]
    assert call_kwargs['status'] == 'UNKNOWN'
