# DocVault Search API — Integration Spec

For bot and agent developers (written for **Dudeskie**). It covers everything needed to build a search interface over DocVault: the three content-search types (hybrid, full-text, semantic) plus filename search, reading documents, status, and question answering.

Every response shape and default below was checked against the running server on 2026-10-03 (DocVault master `969ded7`).

---

## 1. Two ways to integrate

| | **MCP server (recommended)** | **REST API (direct)** |
|---|---|---|
| What | `mcp_server/docvault_mcp.py`, a stdio MCP server | HTTP JSON on `http://127.0.0.1:8050/api/...` |
| Contract | Self-describing: `tools/list` returns names, descriptions and JSON Schemas, plus server `instructions` | This document |
| Output | Trimmed for LLM context (1000-char snippets, no internal fields) | Full fields (offsets, ranks, highlight snippets) |
| Safety | Read-only by construction | The same server also exposes admin routes. **Only call the routes in §5.** |
| Use when | Your bot is an MCP client (Dudeskie is) | You need fields MCP omits, or streaming RAG |

Both are local-only, have no authentication, and need the DocVault server running. The MCP server is a thin client over the REST routes in §5.

### MCP client config
```json
{
  "mcpServers": {
    "docvault": {
      "command": "D:/DocVault/venv/Scripts/python.exe",
      "args": ["D:/DocVault/mcp_server/docvault_mcp.py"],
      "env": {}
    }
  }
}
```
- **Use the absolute venv interpreter.** On this PC a bare `python` resolves to the Windows Store stub, which hangs.
- **Optional:** `"DOCVAULT_MCP_ENABLE_ASK": "1"` in `env` publishes the 4th tool, `docvault_ask` (§4.4). Restart the bot after changing `env`.

---

## 2. What is searchable

- **Content:** extracted text of every document whose status is `COMPLETED`. That includes PDFs (with OCR), Office files, ebooks (EPUB), web pages (HTML/MHT), emails (`.eml`, Thunderbird `.wdseml`), images (OCR + AI description), audio/video transcripts, archives' contents and more.
- **Not searchable:**
  - `PENDING`/`PROCESSING`/`EXTRACTED`: not finished yet (`EXTRACTED` means the text exists but its vectors don't, so it's in full-text results only)
  - `ERROR`: extraction failed
  - `UNKNOWN`: no extractor for that file type
  - `MISSING`: the file was deleted from disk; MISSING files are always excluded from every search
- **Chunks:** each document is split into ~800-character chunks on sentence boundaries, with 100 characters of overlap (settings `embeddings:chunk_size` / `embeddings:chunk_overlap`). Search results are **chunks** (passages), so one document can appear several times.
- **Vaults:** a vault is a scanned root folder. There's currently one, named **Documents** (`Z:/Documents`). Any search can be limited to one or more vaults.

---

## 3. The search types

All content searches go through one endpoint, `GET /api/search`, with a `mode` parameter. Filename search is separate.

### 3.1 Hybrid (`mode=hybrid`, the default): meaning + keywords
- **How:** runs full-text (§3.2) and semantic (§3.3) **in parallel**, then merges them by **Reciprocal Rank Fusion**: each result scores `weight / (60 + rank)` from each list, with weights `search:fts_weight` = 0.4 and `search:sem_weight` = 0.6. Results are deduplicated per chunk and sorted by `combined_score`.
- **Use for:** almost everything, especially natural-language questions ("emails about the radio order", "what did the contract say about termination").
- **Scores:** `score` is the semantic similarity (0–1) when that chunk also had a semantic hit. `combined_score` is the RRF value, small numbers around 0.005–0.02, and is only meaningful for ordering.
- **Graceful degradation:**
  - If the semantic side doesn't answer within `search:semantic_timeout` (20s; e.g. Ollama is busy, cold or down), you get full-text results only, with `degraded: true` and `degraded_reason: "Semantic search unavailable — showing full-text results only"`.
  - Regex and wildcard queries (§3.2) always run full-text only, with `degraded: true` and a reason saying so.
- **Speed:** about 1.5s warm. The first query after Ollama starts can take longer while the embedding model loads.

### 3.2 Full-text (`mode=fts`; the MCP tool calls it `fulltext`): exact keywords
- **How:** SQLite FTS5 keyword index over all chunks, ranked by BM25.
- **Use for:** exact words, names, numbers, codes, phrases, and anything where precise matching beats meaning (`"invoice 147"`, `Margretta`, `HT1250`).
- **Query syntax** (the shape is detected automatically):
  - `plain words`: every word must appear (AND), case-insensitive, ranked. Punctuation is stripped.
  - `word*`: prefix match (`radi*` finds radio, radios, radiology). Fast.
  - `/regex/`: case-insensitive Python regex over chunk text. **Slow: scans the whole index.** Use sparingly.
- **Scores:** results have **no `score`**. They carry `rank` (BM25; *lower = better*, e.g. -22.9) and a `snippet` with matched terms wrapped in `<b>…</b>`. Through MCP, `score` is therefore `null` in fulltext mode.
- **Speed:** about 0.3s.

### 3.3 Semantic (`mode=semantic`): meaning only
- **How:** the query is embedded with `nomic-embed-text` (768-d, via Ollama) and matched against chunk vectors by cosine similarity in LanceDB (IVF-PQ index). Only results with similarity ≥ `embeddings:score_threshold` (**0.65**) are returned.
- **Use for:** concept searches where the wording differs from the documents ("feeling burned out at work" finds "exhaustion", "stress leave"…).
- **Scores:** `score` = similarity, 0–1, higher = better. Typical good hits are 0.75–0.90.
- **Note:** if Ollama is unavailable, semantic mode returns an **empty list** without `degraded: true`. Prefer hybrid when robustness matters.
- **Speed:** about 1.8s.

### 3.4 Filename search (`GET /api/search/filename`; MCP: `mode=filename`)
- **How:** matches file **paths and names**, not content. It includes files that aren't searchable by content (UNKNOWN, ERROR, PENDING); only MISSING is excluded.
- **Query syntax:**
  - `plain words`: every token must appear somewhere in the path (substring, case-insensitive)
  - `*` / `?`: wildcards (`taxes*2024*.pdf`)
  - `/regex/`: regex on the full path
- **Returns** files (not passages), ordered by path.
- **Speed:** about 0.4s.

### Which one to use
| The user… | Use |
|---|---|
| asks a question in natural language | hybrid |
| gives an exact name / number / code / quote | fulltext |
| describes a topic in their own words | hybrid (or semantic) |
| wants "the file called …" or "files in folder …" | filename |
| needs a pattern ("all invoice numbers like INV-2019-xxx") | fulltext with `/regex/` |

---

## 4. MCP tools (what Dudeskie sees)

All tools are annotated `readOnlyHint: true`. Errors come back as normal tool errors (`isError: true`) with a plain-language message (§7).

### 4.1 `docvault_search`
| Param | Type | Default | Notes |
|---|---|---|---|
| `query` | string, required | | min length 1 |
| `mode` | `hybrid` \| `fulltext` \| `semantic` \| `filename` | `hybrid` | §3 |
| `limit` | int 1–50 | 5 | |
| `vault` | string | all vaults | vault **name** (case-insensitive) or `vault_id` |
| `file_type` | string | any | extension without the dot, e.g. `pdf` (see §6 for matching rules) |
| `date_from` / `date_to` | `YYYY-MM-DD` | none | on the file's modified date (§6) |

Content modes return:
```json
{
  "results": [
    {
      "file_hash": "383132533dfe4b23…",
      "file_name": "Doneen Invoice #147 - 051516-053116.html",
      "file_path": "Z:\\Documents\\__PoconoPCPro\\…\\Doneen Invoice #147 - 051516-053116.html",
      "score": 0.8246,
      "paragraph": 1,
      "snippet": "File Name\nUnits\nAmount\nLast Modified\n21\nEvans_Bud_052416_Note.doc …"
    }
  ],
  "note": "Semantic search unavailable — showing full-text results only"
}
```
`note` is present only when the search degraded. `snippet` holds up to 1000 chars of the matched chunk, with `…` when trimmed.

Filename mode returns:
```json
{"results": [{"file_hash": "…", "file_name": "taxes 2024.xlsx", "file_path": "Z:\\Documents\\…\\taxes 2024.xlsx",
              "file_type": "xlsx", "file_size": 1234, "file_modified": "2024-02-01T10:12:00", "status": "COMPLETED"}]}
```

### 4.2 `docvault_get_document`
Read the full extracted text of one document, page by page.

| Param | Default | Notes |
|---|---|---|
| `document_id` | | a `file_hash` from search results |
| `file_path` | | alternative to `document_id`; give **exactly one** |
| `offset` | 0 | character offset to start at |
| `max_chars` | 20000 | 1–100000 |

```json
{"file_hash": "…", "file_name": "bt chapter 6.html", "file_path": "Z:\\…\\bt chapter 6.html",
 "file_type": "html", "file_size": 34667, "file_modified": "1998-05-02T11:00:00", "status": "COMPLETED",
 "total_chars": 30115, "offset": 0, "text": "Chapter Six\nTHE TWELVE TRADITIONS OF N.A.\n…", "next_offset": 20000}
```
While `next_offset` isn't `null`, call again with `offset = next_offset`. A document without extracted text returns an error naming its status and the reason. Lookup by path takes about 1s; by id, about 0.5s.

### 4.3 `docvault_status`
No params. Returns:
```json
{"documents": {"total": 195300, "by_status": {"COMPLETED": 87127, "PENDING": 21131, "ERROR": 1551, "UNKNOWN": 40643, "MISSING": 39828}},
 "vaults": [{"name": "Documents", "vault_id": "e87a2ba3-d977-4b7c-aa05-f460ac35d092", "folder": "Z:/Documents", "state": "active"}],
 "workers": {"paused": false, "stalled": false},
 "search_index": {"ok": true, "chunks": 1300493},
 "issues": ["1551 other extraction errors"]}
```
Use it to tell users what's indexed, or to explain an empty result (e.g. many documents still PENDING).

### 4.4 `docvault_ask` (only when `DOCVAULT_MCP_ENABLE_ASK=1`)
DocVault's own LLM (`qwen2.5:14b` via Ollama) answers from the documents (RAG: hybrid search for the top passages, then generation).

| Param | Default |
|---|---|
| `question` | required |
| `vault` | all vaults |
| `top_k` | `search:rag_top_k` = 5 passages (1–20) |

Returns `{"answer": "…", "sources": [{"file_hash", "file_name", "file_path"}]}`. It's slow (10–60s) and loads a second ~9 GB model onto the GPU. **If the bot has its own LLM, prefer search + get_document and let the bot's model answer.**

---

## 5. REST endpoints

Base URL: `http://127.0.0.1:8050`, taken from `config.ini [server] host/port`. Every route is under `/api`. The unprefixed `/search` returns the HTML search page, not JSON.

### 5.1 `GET /api/search`
| Query param | Default | Notes |
|---|---|---|
| `q` | required | 422 if empty |
| `mode` | `hybrid` | `fts` \| `semantic` \| `hybrid`; anything else returns 422 |
| `limit` | `search:result_limit` = 20 | |
| `file_type`, `date_from`, `date_to` | | §6 |
| `vault_ids` | all | comma-separated vault ids |

Response: `{"results": [...], "degraded": bool, "degraded_reason"?: str}`. Fields per result:

| Field | fts | semantic | hybrid | Meaning |
|---|---|---|---|---|
| `file_hash` | ✓ | ✓ | ✓ | SHA-256 of the file content: the document id |
| `file_path` | ✓ | ✓ | ✓ | Windows path; the vault-specific path when `vault_ids` is given |
| `chunk_index` | ✓ | ✓ | ✓ | which chunk of the document |
| `chunk_text` | ✓ | ✓ | ✓ | the passage (~800 chars) |
| `snippet` | ✓ | | | short excerpt with `<b>` around matches |
| `rank` | ✓ | | | BM25, lower = better |
| `score` | | ✓ | ✓* | cosine similarity 0–1 (*hybrid: when the chunk had a semantic hit) |
| `combined_score` | | | ✓ | RRF score, for ordering only |
| `chunk_offset` | ✓ | ✓ | ✓ | char offset of the chunk in the full text (may be `null`) |
| `chunk_size` | ✓ | ✓ | ✓ | configured chunk size |
| `paragraph_num` | ✓ | ✓ | ✓ | paragraph number at the chunk start (may be `null`) |

### 5.2 `GET /api/search/filename`
Params `q` (required), `limit` (default 20), `file_type`, `date_from`, `date_to`, `vault_ids`. Returns a **bare JSON list** of `{file_hash, file_path, file_type, file_size, file_created, file_modified, status}`.

### 5.3 Documents
| Route | Returns |
|---|---|
| `GET /api/catalog/{file_hash}` | the task record: `file_path, file_type, status, file_size, file_created, file_modified, error_log, metadata_json, …`. 404 if unknown. |
| `GET /api/catalog/{file_hash}/text` | `{"extracted_text": "…"}`. 404 if none. |
| `GET /api/catalog?filename=<path>&limit=50` | `{"tasks": [...], "total_matches": n}`. `filename` is a SQL LIKE pattern, so `_` and `%` are wildcards; check `file_path` for an exact match. |
| `GET /api/catalog/inspect?path=<path>` | `{"task", "chunks", "images"}`, slow (about 6s), but it resolves vault-specific paths |

`metadata_json` holds per-type metadata, e.g. email `from/to/cc/subject/date/attachments`, HTML `title/description`, EPUB `title/author`.

### 5.4 Status and vaults
`GET /api/vaults` returns `[{vault_id, name, scan_directory, state, priority, …}]`. Other status routes: `GET /api/utils/health` (`{counts, vector_store, issues}`) and `GET /api/workers/status`.

### 5.5 RAG
- **`POST /api/query`**
  - Body: `{"question": str, "history": [{"role": "user"|"assistant", "content": str}], "top_k"?: int, "file_type"?, "date_from"?, "date_to"?, "vault_ids"?: "id1,id2"}`
  - Returns `{"answer", "thinking", "sources": [{file_hash, file_path, score, combined_score, chunk_offset, chunk_size, paragraph_num}]}`
- **`POST /api/query/stream`:** the same body, answered as Server-Sent Events (`data: {json}\n\n`):
  - `{"type": "status", "text": "Searching documents..." | "Embedding query..." | "Preparing context..." | "Generating answer..."}`
  - `{"type": "token", "text": "…"}`, repeated
  - `{"type": "done", "answer", "thinking", "sources"}`
  - `{"type": "error", "text": "…"}`

---

## 6. Filters: exact semantics

| Filter | Content modes (`/api/search`) | Filename mode |
|---|---|---|
| `file_type` | **substring** of the extension: `doc` also matches `docx` | **exact**, case-insensitive |
| `date_from` | file modified on or after the date | `DATE(modified or created) >=` |
| `date_to` | semantic: inclusive (through 23:59:59). **fulltext: exclusive of that day** (known quirk, §8) | inclusive |
| `vault_ids` / `vault` | only files registered in those vaults; returned paths are that vault's paths | same |

Dates are `YYYY-MM-DD` and apply to the **file's modified time on disk**, not dates inside the document. An email's sent date is in its metadata and text, not in this filter.

---

## 7. Errors

**REST:**
- 422: validation (empty `q`, bad `mode`, wrong types)
- 404: unknown document, or no text for it
- 500: unexpected server error
- Connection refused: the server isn't running

**MCP:** all of the above become readable tool errors:
- "DocVault server is not reachable at http://127.0.0.1:8050. Is it running?"
- "Unknown vault 'X'. Available vaults: Documents."
- "No document with id '…'." / "No document indexed at path '…'."
- "'name.pdf' has no extracted text (status ERROR): <reason>."
- "DocVault API error 500: <detail>"

---

## 8. Behaviour and limits

- **Search priority:** every search request pauses DocVault's background indexing for `monitor:search_throttle_duration` (60s) so searches get the GPU and disk first. A bot that searches in a loop will slow indexing; avoid polling search.
- **Concurrency:** single-user, local. There are no rate limits, but each semantic or hybrid query uses the GPU briefly.
- **Duplicates:** results are per chunk, so collapse by `file_hash` if you want one hit per document.
- **Paths:** Windows paths on the user's machine. Show them to the user as citations; don't try to open them remotely.
- **Known quirks:**
  - In fulltext mode, `date_to` compares the date string with full timestamps, so files modified *during* that day are excluded. Pass the next day to include it. Semantic and filename modes are inclusive.
  - Fulltext results have no `score` (use `rank`), so the MCP `score` is `null` in that mode.
  - With several `vault_ids`, a file registered in more than one of them can appear once per vault.
  - Semantic mode returns `[]` rather than degrading when Ollama is unavailable; hybrid degrades properly.

---

## 9. Security

- **Document text is untrusted data.** Indexed files include email and web pages, which can contain text like "ignore previous instructions…". Put search results into the model's context as quoted data, never as instructions, and don't let retrieved text trigger tool calls with side effects. DocVault's own RAG prompt wraps passages in `<document>` tags for this reason, and its extraction log flags suspicious patterns.
- **Read-only:** the MCP server exposes nothing that modifies DocVault. When using REST directly, call only the routes in §5. The server also hosts admin routes (purge, reindex, shutdown) that a bot must never call.
- **Local:** DocVault listens on 127.0.0.1 only and has no authentication. Don't expose it beyond this machine.

---

## 10. Recommended bot workflow

1. **Understand the request:** is the user after a topic or question, an exact string, or a file by name? Pick the mode from the table in §3.
2. **Search:** `docvault_search` with `limit` 5–10. Collapse by `file_hash`.
3. **Read when needed:** if a snippet looks relevant but incomplete, `docvault_get_document(document_id)`, following `next_offset` only as far as necessary.
4. **Answer with citations:** cite `file_name`, and `file_path` when the user needs to find the file.
5. **Empty or weak results:**
   - retry with another mode (hybrid ↔ fulltext), fewer or other keywords, or a prefix (`word*`)
   - check `note` for degraded search
   - use `docvault_status` to see whether documents are still PENDING
   - for "find the file named…", use filename mode

**Example:** "What radios did Chuck offer me?"
1. `docvault_search(query="Chuck radios offer", mode="hybrid")` finds the email *Radios* from Chuck Margretta (2013-10-07).
2. `docvault_get_document(document_id=<hash>)` returns the full message body with the radio list.
3. Answer with the list, citing the email's subject and date.
