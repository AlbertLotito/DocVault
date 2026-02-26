import ollama
import configparser
import os

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), '..', 'config.ini'))
EMBED_MODEL = _cfg.get('ollama', 'embed_model', fallback='nomic-embed-text')
OLLAMA_HOST = _cfg.get('ollama', 'host', fallback='http://localhost:11434')


def embed(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using Ollama."""
    try:
        response = ollama.embeddings(model=EMBED_MODEL, prompt=text)
        return response['embedding']
    except Exception as e:
        print(f"  [embed] Error: {e}")
        return None
