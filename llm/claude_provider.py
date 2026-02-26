import httpx
from llm.base import BaseLLMProvider


class ClaudeProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str = 'claude-sonnet-4-6'):
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list[dict]) -> str:
        try:
            resp = httpx.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': self.api_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': self.model,
                    'max_tokens': 1024,
                    'messages': messages,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()['content'][0]['text']
        except Exception as e:
            return f"Claude error: {e}"
