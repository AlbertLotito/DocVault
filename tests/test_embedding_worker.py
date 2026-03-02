from unittest.mock import patch, MagicMock, call
from workers import embedding_worker


@patch('workers.embedding_worker.manager.complete_extraction')
@patch('workers.embedding_worker.VectorStore')
@patch('workers.embedding_worker.embedder.embed')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_embeds_all_chunks(mock_chunk, mock_embed,
                                         mock_vs_class, mock_complete):
    mock_chunk.return_value = ['chunk one', 'chunk two']
    mock_embed.return_value = [0.1] * 768
    mock_vs = MagicMock()
    mock_vs_class.return_value = mock_vs

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': 'chunk one chunk two'
    }
    embedding_worker.process_task('test.db', task, mock_vs)

    assert mock_embed.call_count == 2
    assert mock_vs.upsert.call_count == 2
    mock_complete.assert_called_once_with(
        'test.db', 'abc', status='COMPLETED'
    )


@patch('workers.embedding_worker.manager.complete_extraction')
@patch('workers.embedding_worker.embedder.embed')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_handles_empty_text(mock_chunk, mock_embed, mock_complete):
    mock_chunk.return_value = []
    vs = MagicMock()

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': ''
    }
    embedding_worker.process_task('test.db', task, vs)

    mock_embed.assert_not_called()
    mock_complete.assert_called_once_with(
        'test.db', 'abc', status='COMPLETED'
    )
