# Span Grounding for RAG — Design Spec
**Date:** 2026-04-11
**Status:** Approved

---

## 1. Problem

DocVault's RAG answers tell users *what* the documents say but not *where* in those documents the information comes from. Search results and RAG source citations show a filename and a relevance score; no location data. Users cannot verify an answer, skip to the relevant passage, or understand how much of the document the cited chunk covers.

---

## 2. Goals

- Every search result and RAG source includes the character offset of the retrieved chunk within the full extracted text.
- The LLM receives paragraph-number context per chunk so its answers can include natural-language citations ("paragraph 4").
- A "View in context" inline panel lets users expand any result or RAG source to see the full extracted text with the matched passage highlighted, scrolled into view.

---

## 3. Non-Goals

- Native PDF rendering (PDF.js, page overlays). All display uses extracted text.
- Re-embedding existing documents. Offsets are derived from stored `chunk_index` data.
- Per-chunk offset persistence in Qdrant or SQLite (derived at query time).

> **Implementation prerequisite:** `chunk_text` must be present in both Qdrant payloads and FTS results at query time. Confirm this is the case before starting — the current embedding worker stores `chunk_text` in Qdrant payloads (`embedding_worker.py:99`) and FTS results return `content AS chunk_text` — so this holds.

---

## 4. Approach: Formula-Derived Offset + Text-Search Fallback

The chunker (`embeddings/chunker.py`) is deterministic: chunk `i` starts at:

```
offset = chunk_index × (chunk_size − chunk_overlap)
```

With default settings (`chunk_size=600`, `chunk_overlap=100`), chunk `i` starts at `i × 500`. This is exact for all documents embedded under current settings and requires zero schema changes or re-embedding.

**Validation:** After computing the formula offset, compare `extracted_text[offset : offset + len(chunk_text)]` against `chunk_text`. If they differ by more than a small tolerance (settings may have changed between embed and query), fall back to `extracted_text.find(chunk_text[:120])` for exact location.

**Paragraph number:** `extracted_text[:offset].count('\n\n') + 1`

---

## 5. Components

### 5.1 `search/spans.py` (new)

Single-purpose utility. Public interface:

```python
def resolve_offset(
    chunk_index: int,
    chunk_text: str,
    extracted_text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> int | None:
    """
    Return the start character position of chunk_text within extracted_text.
    Primary: formula offset = chunk_index × (chunk_size − chunk_overlap).
    Fallback: extracted_text.find(chunk_text[:120]) if formula mismatches.
    Returns None if both methods fail.
    """

def paragraph_number(extracted_text: str, offset: int) -> int:
    """Count of double-newline paragraph breaks before offset, plus one."""
```

`resolve_offset` reads `chunk_size` and `chunk_overlap` from `settings.get()` if not passed in.

### 5.2 `GET /api/catalog/{hash}/text` (new endpoint in `api/routes/catalog.py`)

> **Route ordering note:** This specific route must be declared **before** the existing parameterised `GET /api/catalog/{hash}` route in `catalog.py`, per project convention, to avoid the path segment `text` being captured as a hash value.

Returns the full extracted text for a document, used by the inline viewer:

```json
{ "extracted_text": "..." }
```

Reads from `tasks.extracted_text` via `manager`. Returns 404 if hash not found or text is null.

### 5.3 `api/routes/query.py` — RAG endpoint enrichment

After hybrid search returns results:

1. Batch-fetch `extracted_text` for all unique `file_hash` values (single DB query).
2. For each result, call `spans.resolve_offset(chunk_index, chunk_text, extracted_text)`.
3. Compute `paragraph_num = spans.paragraph_number(extracted_text, offset)`.
4. Pass enriched chunks to `llm.rag_query()` as structured dicts instead of plain strings.
5. Return `chunk_offset`, `chunk_size`, and `paragraph_num` in each source entry.

**Updated `/query` response shape:**
```json
{
  "answer": "...",
  "thinking": null,
  "sources": [
    {
      "file_path": "path/to/file.txt",
      "score": 0.87,
      "combined_score": 0.014,
      "chunk_offset": 1500,
      "chunk_size": 600,
      "paragraph_num": 4
    }
  ]
}
```

### 5.4 `llm/base.py` — Prompt enrichment

`rag_query()` signature changes from `chunks: list[str]` to `chunks: list[dict]` where each dict has `text` and `paragraph_num`.

> **Breaking change note:** Grep all call sites of `rag_query()` before implementing — any callers outside `query.py` (e.g., lab or identity pages) must be updated to pass the new dict format.

Each document tag gains a `paragraph` attribute:

```xml
<document index="1" paragraph="4">
...chunk text...
</document>
```

System prompt addition:

> "When citing a source, reference its paragraph number (e.g., 'According to paragraph 4...'). If you cannot determine a location, omit the reference."

### 5.5 `api/routes/search.py` — Search endpoint enrichment

Same offset resolution applied to `/search` results. Each result gains `chunk_offset`, `chunk_size`, `paragraph_num`. Batch-fetch of `extracted_text` done once per search request.

### 5.6 `frontend/search.html` — Inline viewer

**Search results:** Each result card gains a "View in context" toggle button (LCARS `lc-btn-ghost` style). On first expand:

1. `GET /api/catalog/{hash}/text` fetches extracted text.
2. The panel renders the text inside a `<pre>` block with monospace font.
3. The chunk span `[chunk_offset : chunk_offset + chunk_size]` is wrapped in `<mark class="lc-span-highlight">`.
4. The panel scrolls `element.scrollIntoView({ block: 'center' })` on the `<mark>` element.

**RAG sources:** Same panel attached to each source citation in the RAG answer area.

**CSS:** `lc-span-highlight` defined in `lcars.css` — warm amber background (`var(--lc-amber)` at 30% opacity) with slightly darker text, consistent with the Warm LCARS palette.

Fetched text is cached in a JS `Map` keyed by `file_hash` to avoid redundant requests when multiple chunks from the same document are expanded.

---

## 6. Data Flow

```
POST /api/query
  → hybrid.async_search()         → results with chunk_index, chunk_text
  → manager.get_extracted_texts() → {file_hash: extracted_text} batch
  → spans.resolve_offset()        → chunk_offset per result
  → spans.paragraph_number()      → paragraph_num per result
  → llm.rag_query(chunks=[{text, paragraph_num}, ...])
      → <document index="1" paragraph="4">...</document>
      → LLM answer with location citations
  → response: answer + sources[{file_path, score, chunk_offset, chunk_size, paragraph_num}]

GET /api/search
  → same offset resolution after search results
  → response: results[{..., chunk_offset, chunk_size, paragraph_num}]

Frontend expand:
  → GET /api/catalog/{hash}/text
  → render <pre> with <mark> at [chunk_offset : chunk_offset + chunk_size]
  → scrollIntoView()
```

---

## 7. Error Handling

| Failure | Behaviour |
|---|---|
| `resolve_offset` returns `None` | Source included in response; `chunk_offset: null`, `paragraph_num: null`; viewer shows "Location unavailable" |
| `GET /api/catalog/{hash}/text` returns 404 | Viewer shows "Text not available for this document" |
| extracted_text batch fetch fails | Log warning; offsets set to null; RAG answer still returned |
| LLM receives null paragraph_num | `paragraph` attribute omitted from that `<document>` tag; LLM instructed to omit reference if unknown |

---

## 8. New Manager Helper

`manager.get_extracted_texts(db_path, file_hashes: list[str]) -> dict[str, str]`

Single `SELECT file_hash, extracted_text FROM tasks WHERE file_hash IN (...)` query. Returns `{}` on error. Used by both the query and search routes.

---

## 9. Settings

No new settings. Uses existing `embeddings:chunk_size` (default 600) and `embeddings:chunk_overlap` (default 100) read from `settings.get()` at call time in `spans.resolve_offset()`.

---

## 10. Files Changed

| File | Change |
|---|---|
| `search/spans.py` | **New** — `resolve_offset()`, `paragraph_number()` |
| `core/manager.py` | **Add** `get_extracted_texts()` batch helper |
| `api/routes/catalog.py` | **Add** `GET /api/catalog/{hash}/text` endpoint |
| `api/routes/query.py` | Batch-fetch texts, resolve offsets, enrich sources, pass `paragraph_num` to LLM |
| `api/routes/search.py` | Batch-fetch texts, resolve offsets, add `chunk_offset`/`paragraph_num` to results |
| `llm/base.py` | `rag_query()` accepts `list[dict]`; `<document>` tags gain `paragraph` attr; system prompt updated |
| `frontend/search.html` | "View in context" toggle + inline `<pre>/<mark>` viewer for results and RAG sources |
| `frontend/static/lcars.css` | Add `.lc-span-highlight` class |
| `tests/test_span_grounding.py` | **New** — unit + integration tests |

---

## 11. Tests (`tests/test_span_grounding.py`)

1. `test_formula_offset_exact` — formula matches chunker output for chunk 0, 1, N
2. `test_formula_offset_fallback` — mismatch triggers text-search fallback, returns correct offset
3. `test_resolve_offset_none_when_both_fail` — returns None when text not found
4. `test_paragraph_number` — counts `\n\n` correctly at offset 0, mid-doc, and end
5. `test_get_extracted_texts_batch` — returns dict keyed by hash, empty dict on missing hash
6. `test_query_sources_include_offset` — `/query` response sources contain `chunk_offset` and `paragraph_num`
7. `test_search_results_include_offset` — `/search` response contains offset fields
8. `test_catalog_text_endpoint` — returns 200 with `extracted_text`; 404 on unknown hash
9. `test_null_offset_handled_gracefully` — null offset in source does not break response
10. `test_llm_prompt_paragraph_tag` — `rag_query()` dict input produces correct `<document paragraph="N">` XML
