import ollama
from llm.base import BaseLLMProvider
from core.settings import settings


class OllamaProvider(BaseLLMProvider):
    def __init__(self, model: str = 'llama3', host: str = 'http://localhost:11434'):
        self.model = model
        self.host = host

    def chat(self, messages: list[dict]) -> str:
        try:
            from core.monitor import ollama_governor
            options = {
                'temperature':    float(settings.get('ollama:temperature')),
                'num_ctx':        int(settings.get('ollama:num_ctx')),
                'num_predict':    int(settings.get('ollama:num_predict')),
                'top_p':          float(settings.get('ollama:top_p')),
                'repeat_penalty': float(settings.get('ollama:repeat_penalty')),
            }
            raw = settings.get('ollama:chat_timeout')
            timeout = int(raw) if raw not in (None, '', '0', 0) else 300
            client = ollama.Client(host=self.host, timeout=timeout)

            with ollama_governor(kind='chat'):
                response = client.chat(
                    model=self.model,
                    messages=messages,
                    options=options,
                )
                return response['message']['content']

        except Exception as e:
            return f"LLM error: {e}"
