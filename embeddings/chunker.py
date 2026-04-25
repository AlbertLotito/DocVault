import re


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, respecting paragraph boundaries."""
    sentences = []
    for para in re.split(r'\n{2,}', text):
        para = para.strip()
        if not para:
            continue
        parts = re.split(r'(?<=[.!?])\s+', para)
        sentences.extend(p.strip() for p in parts if p.strip())
    return sentences


def _char_split(text: str, max_chars: int, overlap: int) -> list[str]:
    """Character-based fallback for text with no sentence boundaries."""
    chunks = []
    start = 0
    step = max(1, max_chars - overlap)
    while start < len(text):
        chunks.append(text[start:start + max_chars])
        if start + max_chars >= len(text):
            break
        start += step
    return chunks


def chunk(text: str, max_chars: int = None, overlap: int = None) -> list[str]:
    """
    Split text into overlapping chunks at sentence boundaries.
    Falls back to character splitting for sentences that exceed max_chars.
    """
    if max_chars is None or overlap is None:
        from core.settings import settings
        if max_chars is None:
            max_chars = int(settings.get('embeddings:chunk_size') or 1000)
        if overlap is None:
            overlap = int(settings.get('embeddings:chunk_overlap') or 200)

    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)

    # If no sentence boundaries found, fall back to character splitting
    if len(sentences) <= 1:
        return _char_split(text, max_chars, overlap)

    chunks = []
    current: list[str] = []
    current_len = 0

    def _flush():
        nonlocal current, current_len
        if current:
            chunks.append(' '.join(current))
        # Carry back trailing sentences that fit within overlap chars
        carry: list[str] = []
        carry_len = 0
        for s in reversed(current):
            needed = len(s) + (1 if carry else 0)
            if carry_len + needed <= overlap:
                carry.insert(0, s)
                carry_len += needed
            else:
                break
        current = carry
        current_len = carry_len

    for sentence in sentences:
        slen = len(sentence)

        if slen > max_chars:
            # Oversized sentence: flush, then character-split it
            _flush()
            chunks.extend(_char_split(sentence, max_chars, overlap))
            current = []
            current_len = 0
            continue

        space = 1 if current else 0
        if current_len + space + slen > max_chars:
            _flush()

        space = 1 if current else 0
        current.append(sentence)
        current_len += space + slen

    if current:
        chunks.append(' '.join(current))

    return chunks
