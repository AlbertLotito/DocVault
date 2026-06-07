import asyncio
import threading
import time
import ollama
from core.settings import settings

_NO_SLOTS_RETRIES = 4
_NO_SLOTS_BACKOFF = [10, 20, 40, 60]

# Set while a search query is waiting for an embed slot.
# embed_batch() checks this and yields briefly so queries get priority.
_query_embed_event = threading.Event()


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
    """Embed multiple texts in a single Ollama API call — used by the embedding worker.

    Returns one vector per input text.  On failure every entry is None.
    Yields briefly to any pending search query before competing for the Ollama slot.
    """
    from core.monitor import ollama_governor
    client, model = _make_client()

    for attempt in range(_NO_SLOTS_RETRIES + 1):
        # Yield to any pending query embed before competing for the Ollama slot.
        if _query_embed_event.is_set():
            time.sleep(0.5)
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


def query_embed(text: str) -> list[float] | None:
    """Embed a search query — bypasses the worker semaphore and signals workers to yield.

    Uses a short HTTP timeout so failure is fast and FTS-only fallback kicks in
    quickly rather than blocking the query for the full embed_timeout window.
    """
    _query_embed_event.set()
    try:
        model = settings.get('ollama:embed_model')
        raw = settings.get('ollama:query_embed_timeout')
        timeout_secs = int(raw) if raw not in (None, '', '0', 0) else 15
        client = ollama.Client(timeout=timeout_secs)
        response = client.embed(model=model, input=[text], keep_alive=-1)
        return list(response.embeddings[0])
    except Exception as e:
        from core import logger
        logger.warn(f"[embed] Query embed failed: {e}")
        return None
    finally:
        _query_embed_event.clear()


async def async_embed(text: str) -> list[float] | None:
    """Async query-time embed — uses query_embed for priority access over workers."""
    return await asyncio.to_thread(query_embed, text)
