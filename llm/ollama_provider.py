import ollama
from llm.base import BaseLLMProvider
from core.settings import settings


class OllamaProvider(BaseLLMProvider):
    def __init__(self, model: str = 'llama3', host: str = 'http://localhost:11434'):
        self.model = model
        self.host = host

    def chat(self, messages: list[dict]) -> str:
        try:
            options = {
                'temperature':    float(settings.get('ollama:temperature')),
                'num_ctx':        int(settings.get('ollama:num_ctx')),
                'num_predict':    int(settings.get('ollama:num_predict')),
                'top_p':          float(settings.get('ollama:top_p')),
                'repeat_penalty': float(settings.get('ollama:repeat_penalty')),
            }
            response = ollama.chat(
                model=self.model,
                messages=messages,
                options=options,
            )
            return response['message']['content']
        except Exception as e:
            return f"LLM error: {e}"
