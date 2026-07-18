from unittest.mock import patch, MagicMock
import json
from llm.ollama_provider import OllamaProvider
from llm.factory import get_provider
from llm.claude_provider import ClaudeProvider


class TestOllamaProvider:
    @patch('llm.ollama_provider.ollama.Client')
    def test_chat_returns_response(self, mock_client_class):
        mock_instance = MagicMock()
        mock_instance.chat.return_value = {
            'message': {'content': 'The answer is 42.'}
        }
        mock_client_class.return_value = mock_instance
        provider = OllamaProvider(model='llama3')
        result = provider.chat([{'role': 'user', 'content': 'What is the answer?'}])
        assert result == 'The answer is 42.'

    @patch('llm.ollama_provider.ollama.Client')
    def test_chat_returns_error_string_on_failure(self, mock_client_class):
        mock_instance = MagicMock()
        mock_instance.chat.side_effect = Exception("Connection refused")
        mock_client_class.return_value = mock_instance
        provider = OllamaProvider(model='llama3')
        result = provider.chat([{'role': 'user', 'content': 'hello'}])
        assert 'error' in result.lower() or 'Connection refused' in result


class TestGetProvider:
    @patch('llm.factory.settings')
    def test_ollama_provider_uses_settings(self, mock_settings):
        values = {
            'llm:provider': 'ollama',
            'ollama:chat_model': 'qwen2.5:14b',
            'ollama:host': 'http://localhost:11600',
        }
        mock_settings.get.side_effect = lambda key: values[key]
        provider = get_provider()
        assert isinstance(provider, OllamaProvider)
        assert provider.model == 'qwen2.5:14b'
        assert provider.host == 'http://localhost:11600'

    @patch('llm.factory.settings')
    def test_claude_provider_uses_settings(self, mock_settings):
        values = {
            'llm:provider': 'claude',
            'llm:api_key': 'sk-ant-test123',
            'llm:claude_model': 'claude-sonnet-5',
        }
        mock_settings.get.side_effect = lambda key: values[key]
        provider = get_provider()
        assert isinstance(provider, ClaudeProvider)
        assert provider.api_key == 'sk-ant-test123'
        assert provider.model == 'claude-sonnet-5'


class TestClaudeProvider:
    @patch('llm.claude_provider.httpx.stream')
    def test_chat_stream_yields_text_deltas(self, mock_stream):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_lines.return_value = [
            'data: {"type": "message_start"}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}',
            'data: {"type": "message_stop"}',
        ]
        mock_stream.return_value.__enter__.return_value = mock_response
        provider = ClaudeProvider(api_key='test-key')
        chunks = list(provider.chat_stream([{'role': 'user', 'content': 'hi'}]))
        assert chunks == ['Hello', ' world']

    @patch('llm.claude_provider.httpx.stream')
    def test_chat_stream_yields_error_string_on_failure(self, mock_stream):
        mock_stream.side_effect = Exception("Connection refused")
        provider = ClaudeProvider(api_key='test-key')
        chunks = list(provider.chat_stream([{'role': 'user', 'content': 'hi'}]))
        assert len(chunks) == 1
        assert 'Claude error' in chunks[0]
        assert 'Connection refused' in chunks[0]
