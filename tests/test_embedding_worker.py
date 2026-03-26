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


import pytest


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_sends_one_ollama_call(mock_chunk, mock_embed, mock_progress, mock_status):
    """All chunks from all docs go in a single embed_batch call."""
    mock_chunk.side_effect = [['c1', 'c2'], ['c3']]  # doc1=2 chunks, doc2=1 chunk
    mock_embed.return_value = [[0.1]*768, [0.2]*768, [0.3]*768]
    mock_vs = MagicMock()

    tasks = [
        {'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': 'text1'},
        {'file_hash': 'h2', 'file_path': '/b.pdf', 'file_type': 'pdf', 'extracted_text': 'text2'},
    ]
    embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    mock_embed.assert_called_once()
    inputs = mock_embed.call_args[0][0]
    assert len(inputs) == 3  # 2 + 1 chunks total

    assert mock_vs.upsert_batch.call_count == 2  # one per doc
    mock_status.assert_any_call('test.db', 'h1', status='COMPLETED')
    mock_status.assert_any_call('test.db', 'h2', status='COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_completes_empty_docs_without_embed(mock_chunk, mock_embed, mock_progress, mock_status):
    """Empty-text tasks are marked COMPLETED immediately, no embed call."""
    mock_chunk.return_value = []
    mock_vs = MagicMock()

    tasks = [
        {'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': ''},
    ]
    embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    mock_embed.assert_not_called()
    mock_vs.upsert_batch.assert_not_called()
    mock_status.assert_called_once_with('test.db', 'h1', 'COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_skips_none_vectors(mock_chunk, mock_embed, mock_progress, mock_status):
    """None vectors (embed failure) are excluded from upsert, task still completes."""
    mock_chunk.return_value = ['c1', 'c2', 'c3']
    mock_embed.return_value = [[0.1]*768, None, [0.3]*768]
    mock_vs = MagicMock()

    tasks = [{'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': 'text'}]
    embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    batch = mock_vs.upsert_batch.call_args[0][1]
    assert len(batch) == 2
    assert batch[0]['chunk_index'] == 0
    assert batch[1]['chunk_index'] == 2
    mock_status.assert_called_once_with('test.db', 'h1', status='COMPLETED')


@patch('workers.embedding_worker.manager.update_task_status')
@patch('workers.embedding_worker.manager.update_task_progress')
@patch('workers.embedding_worker.embedder.embed_batch')
@patch('workers.embedding_worker.chunker.chunk')
def test_process_task_batch_resets_to_extracted_on_qdrant_failure(mock_chunk, mock_embed, mock_progress, mock_status):
    """If Qdrant upsert raises, ALL non-empty tasks are reset to EXTRACTED and exception re-raises."""
    mock_chunk.side_effect = [['c1'], ['c2']]
    mock_embed.return_value = [[0.1]*768, [0.2]*768]
    mock_vs = MagicMock()
    mock_vs.upsert_batch.side_effect = Exception("Qdrant down")

    tasks = [
        {'file_hash': 'h1', 'file_path': '/a.pdf', 'file_type': 'pdf', 'extracted_text': 'text1'},
        {'file_hash': 'h2', 'file_path': '/b.pdf', 'file_type': 'pdf', 'extracted_text': 'text2'},
    ]
    with pytest.raises(Exception, match="Qdrant down"):
        embedding_worker.process_task_batch('test.db', tasks, mock_vs)

    all_calls = mock_status.call_args_list
    completed = [c for c in all_calls if 'COMPLETED' in str(c)]
    extracted = [c for c in all_calls if 'EXTRACTED' in str(c)]
    assert len(completed) == 0
    assert len(extracted) == 2
