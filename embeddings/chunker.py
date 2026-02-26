def chunk(text: str, max_tokens: int = 500, overlap: int = 50) -> list[str]:
    """
    Split text into overlapping word-based chunks.
    Uses words as a proxy for tokens (1 token ≈ 0.75 words — close enough).
    """
    text = text.strip()
    if not text:
        return []
    words = text.split()
    if len(words) <= max_tokens:
        return [text]
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += max_tokens - overlap
    return chunks
