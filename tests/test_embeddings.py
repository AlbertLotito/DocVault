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
        chunks = chunker.chunk(text, max_tokens=500, overlap=50)
        assert len(chunks) > 1

    def test_overlap_exists_between_chunks(self):
        text = " ".join([f"word{i}" for i in range(200)])
        chunks = chunker.chunk(text, max_tokens=100, overlap=20)
        # Last words of chunk N should appear at start of chunk N+1
        if len(chunks) > 1:
            last_words_c0 = set(chunks[0].split()[-20:])
            first_words_c1 = set(chunks[1].split()[:20])
            assert len(last_words_c0 & first_words_c1) > 0

    def test_empty_text_returns_empty_list(self):
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []


class TestEmbedder:
    @patch('embeddings.embedder.ollama.embeddings')
    def test_embed_returns_vector(self, mock_embed):
        mock_embed.return_value = {'embedding': [0.1, 0.2, 0.3]}
        vec = embedder.embed("hello world")
        assert vec == [0.1, 0.2, 0.3]

    @patch('embeddings.embedder.ollama.embeddings')
    def test_embed_returns_none_on_error(self, mock_embed):
        mock_embed.side_effect = Exception("Ollama unavailable")
        vec = embedder.embed("hello world")
        assert vec is None


class TestVectorStore:
    @patch('embeddings.vector_store.QdrantClient')
    def test_upsert_points(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True
        mock_client_class.return_value = mock_client

        vs = vector_store.VectorStore('localhost', 6333, 'test')
        vs.upsert('hash1', 0, [0.1]*768, {'file_path': '/test.pdf'})
        mock_client.upsert.assert_called_once()

    @patch('embeddings.vector_store.QdrantClient')
    def test_search_returns_results(self, mock_client_class):
        mock_result = MagicMock()
        mock_result.payload = {'file_hash': 'abc', 'chunk_text': 'hello'}
        mock_result.score = 0.95
        mock_client = MagicMock()
        mock_client.collection_exists.return_value = True
        mock_client.search.return_value = [mock_result]
        mock_client_class.return_value = mock_client

        vs = vector_store.VectorStore('localhost', 6333, 'test')
        results = vs.search([0.1]*768, top_k=5)
        assert len(results) == 1
        assert results[0]['score'] == 0.95
