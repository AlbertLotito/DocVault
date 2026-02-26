import configparser, os


def get_provider():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
    provider = cfg.get('llm', 'provider', fallback='ollama')

    if provider == 'ollama':
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(
            model=cfg.get('ollama', 'chat_model', fallback='llama3'),
            host=cfg.get('ollama', 'host', fallback='http://localhost:11434'),
        )
    if provider == 'claude':
        from llm.claude_provider import ClaudeProvider
        return ClaudeProvider(api_key=cfg.get('llm', 'api_key'))
    raise ValueError(f"Unknown LLM provider: {provider}")
