import pytest
from unittest.mock import patch, MagicMock
from embeddings import chunker, embedder, vector_store


class TestChunker:
    def test_short_text_is_single_chunk(self):
        text = "Hello world"
        chunks = chunker.chunk(text)
        assert len(chunks) == 1
        assert chunks[0] == "Hello world"

    def test_long_text_is_split(self):
        text = " ".join(["word"] * 600)  # 600 words
        chunks = chunker.chunk(text, max_chars=500, overlap=50)
        assert len(chunks) > 1

    def test_overlap_exists_between_chunks(self):
        text = " ".join([f"word{i}" for i in range(200)])
        chunks = chunker.chunk(text, max_chars=100, overlap=20)
        # Last words of chunk N should appear at start of chunk N+1
        if len(chunks) > 1:
            last_words_c0 = set(chunks[0].split()[-20:])
            first_words_c1 = set(chunks[1].split()[:20])
            assert len(last_words_c0 & first_words_c1) > 0

    def test_empty_text_returns_empty_list(self):
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []


class TestEmbedder:
    @patch('embeddings.embedder.ollama.Client')
    def test_embed_returns_vector(self, mock_client_class):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1, 0.2, 0.3]]
        mock_client.embed.return_value = mock_response
        mock_client_class.return_value = mock_client
        vec = embedder.embed("hello world")
        assert vec == [0.1, 0.2, 0.3]

    @patch('embeddings.embedder.ollama.Client')
    def test_embed_returns_none_on_error(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.embed.side_effect = Exception("Ollama unavailable")
        mock_client_class.return_value = mock_client
        vec = embedder.embed("hello world")
        assert vec is None

    @patch('embeddings.embedder.ollama.Client')
    def test_embed_batch_returns_all_vectors(self, mock_client_class):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1, 0.2], [0.3, 0.4]]
        mock_client.embed.return_value = mock_response
        mock_client_class.return_value = mock_client
        vecs = embedder.embed_batch(["text one", "text two"])
        assert len(vecs) == 2
        assert vecs[0] == [0.1, 0.2]
        assert vecs[1] == [0.3, 0.4]
        # Single API call regardless of input count
        mock_client.embed.assert_called_once()

    @patch('embeddings.embedder.ollama.Client')
    def test_embed_batch_returns_nones_on_error(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.embed.side_effect = Exception("Ollama unavailable")
        mock_client_class.return_value = mock_client
        vecs = embedder.embed_batch(["a", "b", "c"])
        assert vecs == [None, None, None]


class TestVectorStore:
    def test_upsert_points(self, tmp_path):
        with patch('embeddings.vector_store._db_path', return_value=str(tmp_path)):
            vs = vector_store.VectorStore(collection='test')
            vs.upsert('hash1', 0, [0.1] * 768, {'file_path': '/test.pdf', 'chunk_text': 'hello'})
            assert vs.count() == 1

    def test_search_returns_results(self, tmp_path):
        with patch('embeddings.vector_store._db_path', return_value=str(tmp_path)):
            vs = vector_store.VectorStore(collection='test')
            vec = [0.1] * 768
            vs.upsert('abc', 0, vec, {'file_path': '/test.pdf', 'chunk_text': 'hello'})
            results = vs.search(vec, top_k=5)
            assert len(results) == 1
            assert results[0]['file_hash'] == 'abc'
            assert results[0]['score'] >= 0.99  # near-identical vector → high similarity
