# DocVault MCP Server — Design

**Date:** 2026-10-03
**Status:** Approved (brainstorming, in chat)
**Client:** Dudeskie, the user's chat bot. It runs on the same PC, uses Ollama, and acts as an MCP client.

## Problem

Dudeskie needs to search DocVault. To do that, DocVault has to publish its own contract so the bot can discover the interface without hand-written glue.

The only spec published today is FastAPI's `/openapi.json`. It covers all ~100 routes, including destructive ones (`/api/catalog/missing/purge`, `/api/utils/shutdown`), and it's written for human developers, not for a model's tool selection.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Protocol | **MCP** | Dudeskie is an MCP client. `tools/list` (names, descriptions, JSON Schema) plus the server `instructions` *is* the self-describing contract. |
| Location / auth | **Same PC, no auth** | Loopback only. Nothing listens on a new port. |
| Transport | **stdio, standalone process** | Dudeskie launches `mcp_server/docvault_mcp.py` as a subprocess. Nothing changes in the running server, and all existing validation and vault-path logic is reused. A crash in the MCP layer can't affect DocVault. |
| Tool count | **3 + 1 optional** | Dudeskie's own recommendation: a local Ollama model picks more reliably from a few broad tools than from many narrow ones. |
| `docvault_ask` | **Built, off by default** | Dudeskie has its own LLM. Running DocVault's qwen2.5 as well loads a second ~9 GB model on the same GPU, and one LLM ends up summarizing for another. Enable it per client with `DOCVAULT_MCP_ENABLE_ASK=1`. |
| Naming | **`docvault_` prefix in the tool names** | Unambiguous in any MCP client. |

## Contract

All tools are read-only and annotated `readOnlyHint=True`. No tool opens, modifies, reprocesses, purges or shuts anything down.

### `docvault_search`
- **Params:**
  - `query` (str, required)
  - `mode`: `hybrid` (default) / `fulltext` / `semantic` / `filename`
  - `limit`: 1–50, default 5
  - `vault`: a vault's name or id; optional
  - `file_type`, `date_from`, `date_to`: optional
- **Content modes** (hybrid / fulltext / semantic) call `GET /api/search`, where `fulltext` maps to the API's `fts`. They return a list of `{file_hash, file_name, file_path, score, snippet, paragraph}`. `snippet` is the matched passage, trimmed to 1000 chars.
- **Filename mode** calls `GET /api/search/filename` and returns `{file_hash, file_name, file_path, file_type, file_size, file_modified, status}`.
- **Degraded search:** when the API reports `degraded`, a `note` carries the `degraded_reason`. For example, semantic search was unavailable, so results are full-text only.

### `docvault_get_document`
- **Params:** `document_id` (a file hash) **or** `file_path`, exactly one; `offset` (default 0); `max_chars` (default 20000, max 100000).
- **Path lookup:** a `file_path` is resolved to a hash via `GET /api/catalog/inspect?path=`.
- **Returns:** `{file_hash, file_name, file_path, file_type, file_size, file_modified, status, total_chars, offset, text, next_offset}`. `next_offset` is null at the end of the document.
- **Backed by:** `GET /api/catalog/{hash}` and `GET /api/catalog/{hash}/text`.
- **Not extracted yet:** a readable error that includes the file's status (e.g. PENDING or ERROR).

### `docvault_status`
- **Params:** none.
- **Returns:**
  - `documents`: counts by status
  - `vaults`: `[{name, vault_id, folder, state}]`
  - `workers`: their state
  - `health`: Ollama and database
- **Backed by:** `GET /api/stats`, `/api/vaults`, `/api/workers/status`, `/api/utils/health`.

### `docvault_ask` (only when `DOCVAULT_MCP_ENABLE_ASK=1`)
- **Params:** `question` (required); `vault` and `top_k` (optional).
- **Returns:** `{answer, sources: [{file_name, file_path, file_hash}]}`.
- **Backed by:** `POST /api/query`.
- **Warning in its description:** it's slow, typically 10–60s.

### Vault resolution
`vault` matches a vault id exactly or a vault name case-insensitively. An unknown vault returns an error that lists the valid names.

### Errors
Errors are returned as tool errors with plain-language messages; nothing is raised to the client:
- the server is unreachable: "DocVault server is not reachable at http://127.0.0.1:8050. Is it running?"
- unknown document
- unknown vault
- both or neither of `document_id` / `file_path` given
- any non-2xx response from the API (the message includes its status code and detail)

### Timeouts
Search 30s, get_document 15s, status 10s, ask 300s.

## Architecture

```
Dudeskie ──stdio (JSON-RPC)──► mcp_server/docvault_mcp.py ──HTTP──► DocVault :8050 /api/...
```

- **Files:**
  - `mcp_server/docvault_mcp.py`. The folder is never named `mcp/`, which would shadow the SDK package.
  - `core/server_url.py`: the dependency-free `get_server_url()`, moved out of `tray/tray_app.py` so both the tray and the MCP server share it. The tray module imports tkinter/pystray at import time.
- **SDK:** the official `mcp` Python SDK (`FastMCP`).
- **The stdout rule:** over stdio, stdout is the JSON-RPC channel. The MCP process imports only stdlib, `httpx`, `mcp` and `core.server_url`, never `core.logger` (which prints to stdout) or anything that pulls it in. Its own logging goes to stderr.
- **Testable seams:** pure mapping functions (API JSON → tool output) are separate from the HTTP calls, so they can be unit-tested.
- **Side effect worth keeping:** `/api/search` calls `notify_user_activity()`, so a bot search briefly throttles the background workers, exactly like a human search.

## Client configuration

```json
"docvault": {
  "command": "D:/DocVault/venv/Scripts/python.exe",
  "args": ["D:/DocVault/mcp_server/docvault_mcp.py"],
  "env": {}
}
```

Use the absolute venv interpreter. On this machine a bare `python` resolves to the Windows Store stub, which hangs.

## Testing

- **In-process client:** `tools/list` shows 3 tools by default and 4 with the env var set; correct names, required params and `readOnlyHint`.
- **Each tool:** response mapping and error paths, with HTTP mocked.
- **Integration:** spawn the real script over stdio with the SDK client, handshake and list tools. That's what Dudeskie does.
- **Live:** every tool against the running server and the real vault.

## Out of scope
- Remote/LAN access and authentication.
- Write actions: open file, reprocess, Scan Now.
- The 35% ERROR rate in `/api/stats`, which needs its own investigation.

## Deviations found during implementation (2026-10-03)
- **`docvault_status`:**
  - Document counts come from `/api/utils/health`, not `/api/stats`, because `/api/stats` leaves MISSING files out (39,828 on the live vault).
  - There's no Ollama health endpoint, so `status` reports the vector index (`search_index`) and health issues instead. An Ollama outage still reaches the bot through `docvault_search`'s degraded `note`.
- **`docvault_get_document` by path:** resolved through `GET /api/catalog?filename=` with an exact-match check, because the filter is a SQL LIKE. That takes about 1s, against about 6s for `/api/catalog/inspect`, which also loads vector chunks. `inspect` remains the fallback for vault-specific paths of files shared across vaults.
- **SDK:** `mcp` 2.x (`mcp.server.mcpserver.MCPServer`; v1's `FastMCP` was renamed), pinned `mcp>=2.3,<3`.
