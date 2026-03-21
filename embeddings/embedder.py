import asyncio
import time
import ollama
from core.settings import settings

_NO_SLOTS_RETRIES = 4
_NO_SLOTS_BACKOFF = [10, 20, 40, 60]


def embed(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using Ollama."""
    from core.monitor import ollama_governor
    model           = settings.get('ollama:embed_model')
    timeout_secs    = int(settings.get('ollama:embed_timeout') or 120)

    for attempt in range(_NO_SLOTS_RETRIES + 1):
        try:
            with ollama_governor():
                client   = ollama.Client(timeout=timeout_secs)
                response = client.embeddings(model=model, prompt=text)
                return response['embedding']
        except Exception as e:
            err = str(e)
            if 'no slots' in err.lower() and attempt < _NO_SLOTS_RETRIES:
                wait = _NO_SLOTS_BACKOFF[attempt]
                print(f"  [embed] Ollama busy (no slots), retry {attempt + 1}/{_NO_SLOTS_RETRIES} in {wait}s…")
                time.sleep(wait)
                continue
            print(f"  [embed] Error: {e}")
            return None

async def async_embed(text: str) -> list[float] | None:
    """Async: Generate an embedding vector for the given text using Ollama."""
    return await asyncio.to_thread(embed, text)
