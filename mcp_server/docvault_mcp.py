"""
DocVault MCP server (stdio).

Lets an MCP client (e.g. the Dudeskie chat bot) search and read the documents
DocVault has indexed. The published tool list + JSON schemas + `instructions`
are the contract; clients discover them via the MCP handshake.

This is a thin client over the running DocVault server's REST API
(http://<[server] host>:<port>/api/...), so DocVault must be running.

STDOUT IS THE JSON-RPC CHANNEL. Never print here, and never import modules that
print (core.logger, core.settings, core.manager, ...). Diagnostics go to stderr.

Client config:
    command: D:/DocVault/venv/Scripts/python.exe
    args:    [D:/DocVault/mcp_server/docvault_mcp.py]
    env:     {"DOCVAULT_MCP_ENABLE_ASK": "1"}   # optional, publishes docvault_ask
"""
import os
import sys
from typing import Annotated, Literal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Launched as a script, so only mcp_server/ is on sys.path.
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import httpx  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402
from pydantic import Field  # noqa: E402

from core.server_url import get_server_url  # noqa: E402

ENABLE_ASK_ENV = 'DOCVAULT_MCP_ENABLE_ASK'
SNIPPET_CHARS = 1000

TIMEOUT_SEARCH = 30
TIMEOUT_DOCUMENT = 15
TIMEOUT_STATUS = 10
TIMEOUT_ASK = 300

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)

# Tests swap in an httpx.MockTransport; None means real network.
_transport = None

INSTRUCTIONS = """\
DocVault is the user's local document archive: it has extracted and indexed the text of
their files (PDFs, Office documents, ebooks, email, images via OCR, and more).

Workflow:
1. docvault_search finds relevant passages (or files by name with mode="filename").
2. docvault_get_document reads the full text of a promising result, page by page
   using offset/next_offset.
3. When answering, cite the file_path of each document you used.

docvault_status shows what is indexed (document counts, vaults, worker state).
All tools are read-only. Paths are Windows paths on the user's machine.
"""

SearchMode = Literal['hybrid', 'fulltext', 'semantic', 'filename']
_API_MODE = {'hybrid': 'hybrid', 'fulltext': 'fts', 'semantic': 'semantic'}


# ── HTTP layer ───────────────────────────────────────────────────────────────

class _ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"DocVault API error {status}: {detail}")
        self.status = status
        self.detail = detail


async def _api(method: str, path: str, *, timeout: float, params: dict | None = None,
               json: dict | None = None):
    base = get_server_url()
    params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        async with httpx.AsyncClient(base_url=base, timeout=timeout, transport=_transport,
                                     trust_env=False) as client:
            resp = await client.request(method, path, params=params, json=json)
    except httpx.TimeoutException:
        raise ToolError(f"DocVault did not answer within {timeout:g}s ({method} {path}).")
    except httpx.TransportError:
        raise ToolError(f"DocVault server is not reachable at {base}. Is it running?")
    if resp.status_code >= 400:
        try:
            detail = resp.json().get('detail', resp.text)
        except Exception:
            detail = resp.text
        raise _ApiError(resp.status_code, str(detail))
    return resp.json()


async def _api_or_tool_error(*args, **kwargs):
    try:
        return await _api(*args, **kwargs)
    except _ApiError as e:
        raise ToolError(str(e))


# ── Pure mapping helpers ─────────────────────────────────────────────────────

def _file_name(path: str | None) -> str:
    return (path or '').replace('\\', '/').rsplit('/', 1)[-1]


def _snippet(text: str | None) -> str:
    text = (text or '').strip()
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS] + '…'


def map_passages(results: list) -> list:
    return [{
        'file_hash': r.get('file_hash'),
        'file_name': _file_name(r.get('file_path')),
        'file_path': r.get('file_path'),
        'score': r.get('score'),
        'paragraph': r.get('paragraph_num'),
        'snippet': _snippet(r.get('chunk_text')),
    } for r in results]


def map_files(results: list) -> list:
    return [{
        'file_hash': r.get('file_hash'),
        'file_name': _file_name(r.get('file_path')),
        'file_path': r.get('file_path'),
        'file_type': r.get('file_type'),
        'file_size': r.get('file_size'),
        'file_modified': r.get('file_modified'),
        'status': r.get('status'),
    } for r in results]


def resolve_vault(vaults: list, vault: str) -> str:
    """Vault id for an exact id or a case-insensitive name; ToolError otherwise."""
    for v in vaults:
        if v['vault_id'] == vault:
            return v['vault_id']
    for v in vaults:
        if v['name'].casefold() == vault.strip().casefold():
            return v['vault_id']
    names = ', '.join(v['name'] for v in vaults) or '(none)'
    raise ToolError(f"Unknown vault '{vault}'. Available vaults: {names}.")


async def _vault_id(vault: str | None, timeout: float) -> str | None:
    if not vault:
        return None
    return resolve_vault(await _api_or_tool_error('GET', '/api/vaults', timeout=timeout), vault)


async def _hash_for_path(file_path: str) -> str:
    """file_hash for a document path.

    Fast path: the catalog's file_path filter (~0.4s). It is a SQL LIKE, so '_'/'%'
    in a path can match other files; only an exact match counts. Fallback:
    /api/catalog/inspect (~6s, it also loads vector chunks), which also knows the
    vault-specific paths of files shared across vaults.
    """
    listing = await _api_or_tool_error('GET', '/api/catalog', timeout=TIMEOUT_DOCUMENT,
                                       params={'filename': file_path, 'limit': 50})
    for task in listing.get('tasks', []):
        if task.get('file_path', '').casefold() == file_path.casefold():
            return task['file_hash']
    try:
        found = await _api('GET', '/api/catalog/inspect', params={'path': file_path},
                           timeout=TIMEOUT_DOCUMENT)
    except _ApiError as e:
        if e.status == 404:
            raise ToolError(f"No document indexed at path '{file_path}'.")
        raise ToolError(str(e))
    return found['task']['file_hash']


def ask_enabled() -> bool:
    return os.environ.get(ENABLE_ASK_ENV, '').strip().lower() in ('1', 'true', 'yes', 'on')


# ── Server + tools ───────────────────────────────────────────────────────────

def build_server(enable_ask: bool | None = None) -> MCPServer:
    server = MCPServer('docvault', instructions=INSTRUCTIONS, log_level='WARNING')

    @server.tool(annotations=READ_ONLY)
    async def docvault_search(
        query: Annotated[str, Field(min_length=1, description=(
            'What to look for. Natural-language questions work best in hybrid/semantic mode; '
            'exact words or phrases in fulltext mode; part of a file name in filename mode.'))],
        mode: Annotated[SearchMode, Field(description=(
            'hybrid = meaning + keywords (best default); fulltext = exact keyword match; '
            'semantic = meaning only; filename = match file names/paths instead of content.'))] = 'hybrid',
        limit: Annotated[int, Field(ge=1, le=50, description='Maximum number of results.')] = 5,
        vault: Annotated[str | None, Field(description=(
            'Only search this vault (name or vault_id, see docvault_status). Default: all vaults.'))] = None,
        file_type: Annotated[str | None, Field(description=(
            "Only this file extension, without the dot, e.g. 'pdf', 'docx', 'eml'."))] = None,
        date_from: Annotated[str | None, Field(description='Only files modified on/after this date (YYYY-MM-DD).')] = None,
        date_to: Annotated[str | None, Field(description='Only files modified on/before this date (YYYY-MM-DD).')] = None,
    ) -> dict:
        """Search the user's indexed documents. Content modes return matching passages
        (snippet + file path + score); filename mode returns matching files. Use
        docvault_get_document with a result's file_hash to read more of it."""
        vault_id = await _vault_id(vault, TIMEOUT_SEARCH)
        params = {'q': query, 'limit': limit, 'file_type': file_type,
                  'date_from': date_from, 'date_to': date_to, 'vault_ids': vault_id}
        if mode == 'filename':
            rows = await _api_or_tool_error('GET', '/api/search/filename', params=params,
                                            timeout=TIMEOUT_SEARCH)
            return {'results': map_files(rows)}
        resp = await _api_or_tool_error('GET', '/api/search', params={**params, 'mode': _API_MODE[mode]},
                                        timeout=TIMEOUT_SEARCH)
        out = {'results': map_passages(resp.get('results', []))}
        if resp.get('degraded'):
            out['note'] = resp.get('degraded_reason') or 'Search ran in a degraded mode.'
        return out

    @server.tool(annotations=READ_ONLY)
    async def docvault_get_document(
        document_id: Annotated[str | None, Field(description=(
            'The file_hash of a document (from docvault_search results).'))] = None,
        file_path: Annotated[str | None, Field(description=(
            'Full path of a document, as an alternative to document_id.'))] = None,
        offset: Annotated[int, Field(ge=0, description=(
            'Character position to start reading from; use next_offset from the previous call.'))] = 0,
        max_chars: Annotated[int, Field(ge=1, le=100_000, description='Maximum characters of text to return.')] = 20_000,
    ) -> dict:
        """Read the extracted text of one document, with its metadata (type, size, dates,
        processing status). Long documents are returned in slices: when next_offset is
        not null, call again with offset=next_offset to continue."""
        if not document_id and not file_path:
            raise ToolError('Give a document_id (file_hash) or a file_path.')
        if document_id and file_path:
            raise ToolError('Give document_id or file_path, not both.')

        if file_path:
            document_id = await _hash_for_path(file_path)

        try:
            task = await _api('GET', f'/api/catalog/{document_id}', timeout=TIMEOUT_DOCUMENT)
        except _ApiError as e:
            if e.status == 404:
                raise ToolError(f"No document with id '{document_id}'.")
            raise ToolError(str(e))

        try:
            text = (await _api('GET', f'/api/catalog/{document_id}/text',
                               timeout=TIMEOUT_DOCUMENT))['extracted_text']
        except _ApiError as e:
            if e.status == 404:
                why = f": {task['error_log']}" if task.get('error_log') else ''
                raise ToolError(f"'{_file_name(task.get('file_path'))}' has no extracted text "
                                f"(status {task.get('status')}){why}.")
            raise ToolError(str(e))

        end = offset + max_chars
        return {
            'file_hash': task['file_hash'],
            'file_name': _file_name(task.get('file_path')),
            'file_path': task.get('file_path'),
            'file_type': task.get('file_type'),
            'file_size': task.get('file_size'),
            'file_modified': task.get('file_modified'),
            'status': task.get('status'),
            'total_chars': len(text),
            'offset': offset,
            'text': text[offset:end],
            'next_offset': end if end < len(text) else None,
        }

    @server.tool(annotations=READ_ONLY)
    async def docvault_status() -> dict:
        """What DocVault has indexed: document counts by processing status, the vaults
        (searchable folders) with their names and ids, whether the background workers
        are running, and any health issues."""
        health = await _api_or_tool_error('GET', '/api/utils/health', timeout=TIMEOUT_STATUS)
        vaults = await _api_or_tool_error('GET', '/api/vaults', timeout=TIMEOUT_STATUS)
        workers = await _api_or_tool_error('GET', '/api/workers/status', timeout=TIMEOUT_STATUS)
        counts = health.get('counts', {})
        index = health.get('vector_store') or {}
        return {
            'documents': {'total': sum(counts.values()), 'by_status': counts},
            'vaults': [{'name': v['name'], 'vault_id': v['vault_id'],
                        'folder': v.get('scan_directory'), 'state': v.get('state')} for v in vaults],
            'workers': {'paused': workers.get('paused'), 'stalled': workers.get('stalled')},
            'search_index': {'ok': index.get('ok'), 'chunks': index.get('points')},
            'issues': [i['title'] for i in health.get('issues', [])],
        }

    if enable_ask if enable_ask is not None else ask_enabled():
        @server.tool(annotations=READ_ONLY)
        async def docvault_ask(
            question: Annotated[str, Field(min_length=1, description='The question to answer from the documents.')],
            vault: Annotated[str | None, Field(description='Only use this vault (name or vault_id).')] = None,
            top_k: Annotated[int | None, Field(ge=1, le=20, description=(
                'How many passages DocVault retrieves as context. Default: its configured value.'))] = None,
        ) -> dict:
            """Have DocVault's own language model answer a question from the user's documents,
            with the source files it used. Slow (typically 10-60 seconds) and uses the GPU;
            prefer docvault_search + docvault_get_document when you can reason over passages yourself."""
            body = {'question': question, 'vault_ids': await _vault_id(vault, TIMEOUT_ASK), 'top_k': top_k}
            resp = await _api_or_tool_error('POST', '/api/query', timeout=TIMEOUT_ASK,
                                            json={k: v for k, v in body.items() if v is not None})
            return {
                'answer': resp.get('answer'),
                'sources': [{'file_hash': s.get('file_hash'), 'file_name': _file_name(s.get('file_path')),
                             'file_path': s.get('file_path')} for s in resp.get('sources', [])],
            }

    return server


if __name__ == '__main__':
    build_server().run()   # stdio
