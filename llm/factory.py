from core.settings import settings


def get_provider():
    provider = settings.get('llm:provider')

    if provider == 'ollama':
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(
            model=settings.get('ollama:chat_model'),
            host=settings.get('ollama:host'),
        )
    if provider == 'claude':
        from llm.claude_provider import ClaudeProvider
        return ClaudeProvider(
            api_key=settings.get('llm:api_key'),
            model=settings.get('llm:claude_model'),
        )
    raise ValueError(f"Unknown LLM provider: {provider}")
