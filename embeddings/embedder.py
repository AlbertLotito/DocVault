import asyncio
import time
import ollama
from core.settings import settings

_NO_SLOTS_RETRIES = 4
_NO_SLOTS_BACKOFF = [10, 20, 40, 60]


def _make_client() -> tuple:
    """Return (client, model) with settings read at call time."""
    model = settings.get('ollama:embed_model')
    raw = settings.get('ollama:embed_timeout')
    timeout_secs = int(raw) if raw not in (None, '', '0', 0) else 120
    return ollama.Client(timeout=timeout_secs), model


def embed(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text using Ollama."""
    result = embed_batch([text])
    return result[0]


def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed multiple texts in a single Ollama API call.

    Returns one vector per input text.  On failure every entry is None.
    Uses /api/embed (batch endpoint) — one HTTP round-trip regardless of
    how many texts are supplied.
    """
    from core.monitor import ollama_governor
    client, model = _make_client()

    for attempt in range(_NO_SLOTS_RETRIES + 1):
        try:
            with ollama_governor(kind='embed'):
                response = client.embed(model=model, input=texts, keep_alive=-1)
                return [list(v) for v in response.embeddings]
        except Exception as e:
            err = str(e)
            if 'no slots' in err.lower() and attempt < _NO_SLOTS_RETRIES:
                wait = _NO_SLOTS_BACKOFF[attempt]
                print(f"  [embed] Ollama busy (no slots), retry {attempt + 1}/{_NO_SLOTS_RETRIES} in {wait}s…")
                time.sleep(wait)
                continue
            print(f"  [embed] Error: {e}")
            return [None] * len(texts)


async def async_embed(text: str) -> list[float] | None:
    """Async: Generate an embedding vector for the given text using Ollama."""
    return await asyncio.to_thread(embed, text)
