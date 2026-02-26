import ollama
from llm.base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    def __init__(self, model: str = 'llama3', host: str = 'http://localhost:11434'):
        self.model = model
        self.host = host

    def chat(self, messages: list[dict]) -> str:
        try:
            response = ollama.chat(model=self.model, messages=messages)
            return response['message']['content']
        except Exception as e:
            return f"LLM error: {e}"
