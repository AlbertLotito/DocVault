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
        import configparser, os
        from llm.claude_provider import ClaudeProvider
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
        return ClaudeProvider(api_key=cfg.get('llm', 'api_key'))
    raise ValueError(f"Unknown LLM provider: {provider}")
