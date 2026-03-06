import asyncio
import ollama
from core.settings import settings


def embed(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using Ollama."""
    try:
        from core.monitor import ollama_governor
        model = settings.get('ollama:embed_model')

        with ollama_governor():
            response = ollama.embeddings(model=model, prompt=text)
            return response['embedding']

    except Exception as e:
        print(f"  [embed] Error: {e}")
        return None

async def async_embed(text: str) -> list[float] | None:
    """Async: Generate an embedding vector for the given text using Ollama."""
    return await asyncio.to_thread(embed, text)
