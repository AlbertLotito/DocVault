from unittest.mock import patch, MagicMock
from workers import embedding_worker


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_embeds_all_chunks(mock_chunk, mock_embed_batch,
                                        mock_progress, mock_status):
    mock_chunk.return_value = ['chunk one', 'chunk two']
    mock_embed_batch.return_value = [[0.1] * 768, [0.2] * 768]
    mock_vs = MagicMock()

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': 'chunk one chunk two'
    }
    embedding_worker.process_task('test.db', task, mock_vs)

    # Single batch call to Ollama, not one per chunk
    mock_embed_batch.assert_called_once()
    inputs = mock_embed_batch.call_args[0][0]
    assert len(inputs) == 2

    # Single batch upsert to Qdrant
    mock_vs.upsert_batch.assert_called_once()
    batch = mock_vs.upsert_batch.call_args[0][1]
    assert len(batch) == 2

    mock_status.assert_called_once_with('test.db', 'abc', status='COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_handles_empty_text(mock_chunk, mock_embed_batch,
                                         mock_progress, mock_status):
    mock_chunk.return_value = []
    mock_vs = MagicMock()

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': ''
    }
    embedding_worker.process_task('test.db', task, mock_vs)

    mock_embed_batch.assert_not_called()
    mock_vs.upsert_batch.assert_not_called()
    mock_status.assert_called_once_with('test.db', 'abc', 'COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_skips_failed_vectors(mock_chunk, mock_embed_batch,
                                           mock_progress, mock_status):
    """If embed_batch returns some Nones, only non-None vectors are upserted."""
    mock_chunk.return_value = ['chunk one', 'chunk two', 'chunk three']
    mock_embed_batch.return_value = [[0.1] * 768, None, [0.3] * 768]
    mock_vs = MagicMock()

    task = {
        'file_hash': 'abc', 'file_path': '/test.pdf',
        'file_type': 'pdf', 'extracted_text': 'some text'
    }
    embedding_worker.process_task('test.db', task, mock_vs)

    batch = mock_vs.upsert_batch.call_args[0][1]
    assert len(batch) == 2
    assert batch[0]['chunk_index'] == 0
    assert batch[1]['chunk_index'] == 2
    mock_status.assert_called_once_with('test.db', 'abc', status='COMPLETED')
