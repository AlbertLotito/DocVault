from unittest.mock import patch, MagicMock
from llm.ollama_provider import OllamaProvider


class TestOllamaProvider:
    @patch('llm.ollama_provider.ollama.chat')
    def test_chat_returns_response(self, mock_chat):
        mock_chat.return_value = {
            'message': {'content': 'The answer is 42.'}
        }
        provider = OllamaProvider(model='llama3')
        result = provider.chat([{'role': 'user', 'content': 'What is the answer?'}])
        assert result == 'The answer is 42.'

    @patch('llm.ollama_provider.ollama.chat')
    def test_chat_returns_error_string_on_failure(self, mock_chat):
        mock_chat.side_effect = Exception("Connection refused")
        provider = OllamaProvider(model='llama3')
        result = provider.chat([{'role': 'user', 'content': 'hello'}])
        assert 'error' in result.lower() or 'Connection refused' in result
