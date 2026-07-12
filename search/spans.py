"""
Span grounding utilities for RAG and search.

Resolves character offsets for LanceDB/FTS chunks using a two-step strategy:
  1. Formula: offset = chunk_index × (chunk_size − chunk_overlap)  [exact under normal conditions]
  2. Fallback: str.find() on chunk_text[:120]                      [handles settings-change edge cases]
"""

_PROBE_LEN = 120  # long enough to be unique in real documents, short enough for sub-chunk-size chunks


def resolve_offset(
    chunk_index: int,
    chunk_text: str,
    extracted_text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> int | None:
    """
    Return the start character position of chunk_text within extracted_text.

    Primary path: deterministic formula based on chunk_index.
    Fallback: extracted_text.find(chunk_text[:120]) when formula mismatches.
    Returns None when both paths fail.
    """
    if not extracted_text or not chunk_text:
        return None

    if chunk_size is None or chunk_overlap is None:
        from core.settings import settings
        if chunk_size is None:
            chunk_size = int(settings.get('embeddings:chunk_size') or 600)
        if chunk_overlap is None:
            chunk_overlap = int(settings.get('embeddings:chunk_overlap') or 100)

    stride = chunk_size - chunk_overlap
    offset = chunk_index * stride

    # Validate formula offset: text at that position must match chunk_text exactly
    if 0 <= offset < len(extracted_text):
        candidate = extracted_text[offset: offset + len(chunk_text)]
        if candidate == chunk_text:
            return offset

    # Fallback: substring search using first _PROBE_LEN chars as probe
    probe = chunk_text[:_PROBE_LEN]
    idx = extracted_text.find(probe)
    if idx >= 0:
        return idx

    return None


def paragraph_number(extracted_text: str, offset: int) -> int:
    """
    Return the 1-based paragraph number of the character at `offset`.
    Paragraphs are delimited by double newlines (\\n\\n).
    """
    return extracted_text[:offset].count('\n\n') + 1
