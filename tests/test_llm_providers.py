from unittest.mock import patch, MagicMock
from llm.ollama_provider import OllamaProvider


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
