# DocVault MCP Server

DocVault publishes its search as a [Model Context Protocol](https://modelcontextprotocol.io) server. Any MCP client, such as a chat bot (Dudeskie), Claude Desktop or Claude Code, can then search and read your indexed documents.

The contract describes itself. During the MCP handshake the client calls `tools/list` and receives every tool's name, description and JSON Schema, plus the server's usage `instructions`. You never have to hand-write tool definitions on the client side.

## How it runs

```
chat bot ──stdio (JSON-RPC)──► mcp_server/docvault_mcp.py ──HTTP──► DocVault :8050 /api/...
```

- **Launched by the client:** the client starts `mcp_server/docvault_mcp.py` as a subprocess and talks to it over stdin/stdout.
- **A thin layer over the REST API:** it calls the running DocVault server's REST API, so **DocVault must be running**. If it isn't, every tool returns "DocVault server is not reachable at http://127.0.0.1:8050. Is it running?".
- **Local only:** it reads the server address from `config.ini` `[server]`. No authentication is needed, because everything stays on this machine.
- **Read-only:** no tool can open, modify, reprocess, purge or delete anything, and every tool is annotated `readOnlyHint`.

## Client configuration

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

- **Use the full path to the venv interpreter.** On Windows a bare `python` can resolve to the Microsoft Store stub, which hangs forever instead of starting the server.
- **No working directory needed.** The script finds the project on its own, so `cwd` doesn't matter.
- **Enabling `docvault_ask`:** add `"DOCVAULT_MCP_ENABLE_ASK": "1"` to `env` (see below). The client reads the tool list at startup, so restart the client after changing it.

## Tools

### `docvault_search`
Search the indexed documents.

| Param | Default | Meaning |
|---|---|---|
| `query` | required | What to look for |
| `mode` | `hybrid` | `hybrid` (meaning + keywords), `fulltext` (exact keywords), `semantic` (meaning only), or `filename` (match file names/paths) |
| `limit` | 5 | 1–50 |
| `vault` | all | Vault name (case-insensitive) or `vault_id` |
| `file_type` | any | Extension without the dot, e.g. `pdf` |
| `date_from` / `date_to` | — | `YYYY-MM-DD`, on the file's modified date |

- **Content modes** return `{results: [{file_hash, file_name, file_path, score, paragraph, snippet}]}`. Snippets are capped at 1000 chars.
- **Degraded search:** `note` explains when search fell back to full-text only, for example because Ollama was unavailable.
- **Filename mode** returns `{results: [{file_hash, file_name, file_path, file_type, file_size, file_modified, status}]}`.

### `docvault_get_document`
Read one document's extracted text and metadata.

| Param | Default | Meaning |
|---|---|---|
| `document_id` | — | A `file_hash` from search results |
| `file_path` | — | Alternative to `document_id`; give exactly one |
| `offset` | 0 | Where to start reading |
| `max_chars` | 20000 | 1–100000 |

- **Returns** `{file_hash, file_name, file_path, file_type, file_size, file_modified, status, total_chars, offset, text, next_offset}`.
- **Paging:** while `next_offset` is not null, call again with `offset=next_offset`.
- **No text yet:** a document that has no extracted text (still pending, or failed) returns an error with its status and failure reason.

### `docvault_status`
What's indexed. Returns:
- `documents`: total and `by_status` (COMPLETED / ERROR / MISSING / PENDING / …)
- `vaults`: `[{name, vault_id, folder, state}]`
- `workers`: `{paused, stalled}`
- `search_index`: `{ok, chunks}`
- `issues`: health-check titles

### `docvault_ask` (opt-in)
DocVault's own language model answers a question from your documents and returns `{answer, sources: [{file_hash, file_name, file_path}]}`. Params: `question`, plus optional `vault` and `top_k` (1–20).

It's off by default. A chat bot normally has its own LLM, and if that bot also runs on this PC's GPU, `ask` would load a second large model alongside it. It's also slow, typically 10–60s. Enable it with `DOCVAULT_MCP_ENABLE_ASK=1`.

## Errors

Errors come back as normal MCP tool errors (`isError: true`) with a plain-language message the model can relay or act on:
- the server isn't running
- an unknown vault (the message lists the valid names)
- an unknown document or path
- a document with no extracted text
- invalid parameters
- an API failure (with its status code and detail)

## Development

- **Code:** `mcp_server/docvault_mcp.py`, built on the official `mcp` Python SDK (2.x, `MCPServer`).
- **Tests:** `tests/test_mcp_server.py`. They drive every tool through a real in-process MCP client against a fake API that only accepts real `/api/` routes from `api.main.app`, plus a real stdio subprocess handshake.
- **Never print to stdout in this module.** Stdout is the JSON-RPC channel. For the same reason it must not import `core.logger`, `core.settings` or `core.manager`; it imports only `core.server_url`, which a test keeps dependency-free.
