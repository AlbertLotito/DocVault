def chunk(text: str, max_chars: int = None, overlap: int = None) -> list[str]:
    """
    Split text into overlapping character-based chunks.
    max_chars and overlap default to settings values if not specified.
    """
    if max_chars is None or overlap is None:
        from core.settings import settings
        if max_chars is None:
            max_chars = int(settings.get('embeddings:chunk_size') or 600)
        if overlap is None:
            overlap = int(settings.get('embeddings:chunk_overlap') or 100)

    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += max_chars - overlap
    return chunks
