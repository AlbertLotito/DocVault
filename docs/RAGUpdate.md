# DocVault: RAG Strategy Update — Chunk-Level Hybrid Search

**Date:** 2026-03-04
**Status:** Proposed Architecture
**Goal:** Improve RAG precision by aligning Keyword (FTS5) and Semantic (Vector) search at the chunk level.

---

## 1. The Problem: Granularity Mismatch

Currently, DocVault implements Hybrid Search, but the two "branches" operate at different levels of granularity:
*   **Semantic Search (Qdrant)**: Indexes **individual chunks** (default 600 chars). It can pinpoint a specific passage.
*   **Keyword Search (FTS5)**: Indexes the **entire document** as a single block of text.

### **The Consequences:**
1.  **Diluted Relevance**: A keyword match in a 100-page document carries the same "weight" as a match in a 1-page document, even if the keyword appears only once in a non-relevant section.
2.  **RAG Context Gaps**: When performing RAG, we retrieve the top *chunks* from Qdrant. If a document is a strong Keyword match but a weak Semantic match, we might miss the specific chunk that contained the keyword because the RRF fusion is currently performed at the *file level*.
3.  **Coordinate Mismatch**: There is no direct link between a "High Keyword Score" for a file and a "High Semantic Score" for a specific chunk within that file.

---

## 2. The Solution: Chunk-Level Hybrid Search

We will move the Keyword Index (SQLite FTS5) to the **chunk level**, creating a 1-to-1 mapping between Vector points and FTS5 rows.

### **2.1 New Data Architecture**
The `fts_index` table will be reconstructed:
*   **Old Schema**: `(file_hash, file_path, content)` — 1 row per file.
*   **New Schema**: `(file_hash, chunk_index, content)` — 1 row per chunk.

### **2.2 Alignment Strategy**
To ensure Reciprocal Rank Fusion (RRF) works correctly, the **Chunker** must be the single source of truth:
1.  When a file is processed, it is split into `N` chunks using the configured `embeddings:chunk_size` and `embeddings:chunk_overlap`.
2.  **Path A (Semantic)**: Each chunk is embedded and sent to Qdrant with `file_hash` and `chunk_index` in the payload.
3.  **Path B (Keyword)**: Each chunk is inserted into the FTS5 `fts_index` with the same `file_hash` and `chunk_index`.

---

## 3. Benefits to RAG Performance

1.  **Pinpoint Retrieval**: The search engine can now identify that *Chunk #42* of *Document X* is the highest-scoring passage because it contains the exact technical term (Keyword) AND matches the user's intent (Semantic).
2.  **Improved RRF Fusion**: RRF will now be calculated per **(file_hash, chunk_index)**. A chunk that appears in the top 10 for both keyword and semantic searches will almost certainly be the first context passed to the LLM.
3.  **Context Window Efficiency**: By feeding the LLM only the most relevant *hybrid* chunks, we reduce "noise" in the context window, leading to more accurate and factual answers.

---

## 4. Implementation Constraints (The "No Re-Embedding" Rule)

*   **No New Vectors**: Existing embeddings in Qdrant are preserved. We will simply "backfill" the FTS5 index using the existing text and the same chunking logic.
*   **Idempotent Migration**: The migration will involve clearing the old `fts_index` and iterating through all `COMPLETED` tasks to re-chunk and re-index their text.
*   **Performance**: Since this is a local SQLite operation with no LLM calls, the update for an existing corpus of thousands of documents should complete in minutes.

---

## 5. Next Steps

1.  **Update `manager.py`**: Modify the `fts_index` schema and the `complete_extraction` logic.
2.  **Update `hybrid.py`**: Refactor the `merge()` function to fuse results at the chunk level.
3.  **Migration Script**: Create a utility to convert document-level FTS entries to chunk-level entries for existing data.
