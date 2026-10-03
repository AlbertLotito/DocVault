"""
Tests for mcp_server/docvault_mcp.py — the DocVault MCP stdio server.

Tools are exercised through a real in-process MCP client against a fake
DocVault HTTP API (httpx.MockTransport). The fake only answers paths that
exist in DocVault's real route table, so a wrong URL (e.g. a missing /api
prefix) fails here rather than against the live server.
"""
import json
import os
import sys

import httpx
import pytest
from mcp.client import Client
from starlette.routing import Match

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from mcp_server import docvault_mcp  # noqa: E402

VAULTS = [
    {'vault_id': 'v-docs', 'name': 'Documents', 'scan_directory': 'Z:/Documents', 'state': 'active'},
    {'vault_id': 'v-mail', 'name': 'Mail Archive', 'scan_directory': 'Z:/Mail', 'state': 'archived'},
]


def _real_route_exists(method, path):
    from api.main import app
    scope = {'type': 'http', 'path': path, 'method': method}
    return any(r.matches(scope)[0] == Match.FULL for r in app.routes)


class FakeApi:
    """Routes (METHOD, path) -> handler(request) -> (status, json)."""

    def __init__(self):
        self.routes = {('GET', '/api/vaults'): lambda req: (200, VAULTS)}
        self.requests = []

    def on(self, method, path, status=200, body=None, fn=None):
        self.routes[(method, path)] = fn or (lambda req: (status, body))

    def __call__(self, request):
        self.requests.append(request)
        key = (request.method, request.url.path)
        # /api/ check matters: unprefixed /search is a real route too (the HTML page).
        assert key[1].startswith('/api/'), f"MCP server called a non-API route: {key}"
        assert _real_route_exists(*key), f"MCP server called a route DocVault doesn't have: {key}"
        if key not in self.routes:
            return httpx.Response(599, json={'detail': f'unexpected call {key}'})
        status, body = self.routes[key](request)
        return httpx.Response(status, json=body)

    def last(self, path):
        return [r for r in self.requests if r.url.path == path][-1]


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(docvault_mcp, '_transport', httpx.MockTransport(fake))
    monkeypatch.setattr(docvault_mcp, 'get_server_url', lambda: 'http://127.0.0.1:8050')
    return fake


async def _call(tool, args, enable_ask=False):
    async with Client(docvault_mcp.build_server(enable_ask=enable_ask)) as c:
        result = await c.call_tool(tool, args)
    text = result.content[0].text
    return result.is_error, (text if result.is_error else json.loads(text))


# ── The published contract (tools/list) ──────────────────────────────────────

async def _tools(enable_ask):
    async with Client(docvault_mcp.build_server(enable_ask=enable_ask)) as c:
        return {t.name: t for t in (await c.list_tools()).tools}, c.instructions


async def test_publishes_three_read_only_tools_by_default():
    tools, _ = await _tools(enable_ask=False)
    assert set(tools) == {'docvault_search', 'docvault_get_document', 'docvault_status'}
    for t in tools.values():
        assert t.annotations.read_only_hint is True
        assert t.description and len(t.description) > 40


async def test_ask_is_published_only_when_enabled():
    tools, _ = await _tools(enable_ask=True)
    assert 'docvault_ask' in tools
    assert tools['docvault_ask'].annotations.read_only_hint is True


def test_ask_toggle_reads_env_var(monkeypatch):
    monkeypatch.setenv('DOCVAULT_MCP_ENABLE_ASK', '1')
    assert docvault_mcp.ask_enabled() is True
    monkeypatch.setenv('DOCVAULT_MCP_ENABLE_ASK', '0')
    assert docvault_mcp.ask_enabled() is False
    monkeypatch.delenv('DOCVAULT_MCP_ENABLE_ASK')
    assert docvault_mcp.ask_enabled() is False


async def test_search_schema_describes_its_parameters():
    tools, _ = await _tools(enable_ask=False)
    schema = tools['docvault_search'].input_schema
    assert schema['required'] == ['query']
    props = schema['properties']
    assert set(props['mode']['enum']) == {'hybrid', 'fulltext', 'semantic', 'filename'}
    assert props['limit']['default'] == 5
    assert (props['limit']['minimum'], props['limit']['maximum']) == (1, 50)
    for name in ('query', 'mode', 'limit', 'vault'):
        assert props[name].get('description'), f"{name} has no description"


async def test_server_publishes_usage_instructions():
    _, instructions = await _tools(enable_ask=False)
    assert 'docvault_search' in instructions and 'docvault_get_document' in instructions


# ── docvault_search ──────────────────────────────────────────────────────────

SEARCH_HIT = {
    'score': 0.84, 'combined_score': 0.0098, 'file_hash': 'h1',
    'file_path': 'Z:\\Documents\\Bills\\invoice-june.pdf',
    'chunk_text': 'Invoice total ' + 'x' * 3000, 'chunk_index': 6,
    'chunk_offset': 3872, 'chunk_size': 800, 'paragraph_num': 2,
}


async def test_search_returns_trimmed_passages(api):
    api.on('GET', '/api/search', body={'results': [SEARCH_HIT], 'degraded': False, 'degraded_reason': ''})

    err, out = await _call('docvault_search', {'query': 'invoice'})

    assert not err
    assert out['results'] == [{
        'file_hash': 'h1', 'file_name': 'invoice-june.pdf',
        'file_path': 'Z:\\Documents\\Bills\\invoice-june.pdf',
        'score': 0.84, 'paragraph': 2,
        'snippet': SEARCH_HIT['chunk_text'][:docvault_mcp.SNIPPET_CHARS] + '…',
    }]
    assert 'note' not in out
    params = api.last('/api/search').url.params
    assert (params['q'], params['mode'], params['limit']) == ('invoice', 'hybrid', '5')


async def test_fulltext_mode_maps_to_the_api_fts_mode(api):
    api.on('GET', '/api/search', body={'results': [], 'degraded': False})
    await _call('docvault_search', {'query': 'x', 'mode': 'fulltext'})
    assert api.last('/api/search').url.params['mode'] == 'fts'


async def test_degraded_search_carries_a_note(api):
    api.on('GET', '/api/search', body={'results': [], 'degraded': True,
                                       'degraded_reason': 'Semantic search unavailable'})
    err, out = await _call('docvault_search', {'query': 'x'})
    assert not err
    assert out['note'] == 'Semantic search unavailable'


async def test_filename_mode_uses_filename_search(api):
    api.on('GET', '/api/search/filename', body=[{
        'file_hash': 'h2', 'file_path': 'Z:\\Documents\\taxes 2024.xlsx', 'file_type': 'xlsx',
        'file_size': 1234, 'file_created': '2024-01-01', 'file_modified': '2024-02-01', 'status': 'COMPLETED'}])

    err, out = await _call('docvault_search', {'query': 'taxes', 'mode': 'filename', 'limit': 3})

    assert not err
    assert out['results'] == [{'file_hash': 'h2', 'file_name': 'taxes 2024.xlsx',
                               'file_path': 'Z:\\Documents\\taxes 2024.xlsx', 'file_type': 'xlsx',
                               'file_size': 1234, 'file_modified': '2024-02-01', 'status': 'COMPLETED'}]
    assert api.last('/api/search/filename').url.params['limit'] == '3'


async def test_vault_can_be_given_by_name_case_insensitively(api):
    api.on('GET', '/api/search', body={'results': [], 'degraded': False})
    await _call('docvault_search', {'query': 'x', 'vault': 'mail archive'})
    assert api.last('/api/search').url.params['vault_ids'] == 'v-mail'


async def test_vault_can_be_given_by_id(api):
    api.on('GET', '/api/search', body={'results': [], 'degraded': False})
    await _call('docvault_search', {'query': 'x', 'vault': 'v-docs'})
    assert api.last('/api/search').url.params['vault_ids'] == 'v-docs'


async def test_unknown_vault_lists_the_valid_names(api):
    err, msg = await _call('docvault_search', {'query': 'x', 'vault': 'Nope'})
    assert err
    assert "Unknown vault 'Nope'" in msg and 'Documents' in msg and 'Mail Archive' in msg


async def test_filters_are_passed_through(api):
    api.on('GET', '/api/search', body={'results': [], 'degraded': False})
    await _call('docvault_search', {'query': 'x', 'file_type': 'pdf',
                                    'date_from': '2024-01-01', 'date_to': '2024-12-31'})
    p = api.last('/api/search').url.params
    assert (p['file_type'], p['date_from'], p['date_to']) == ('pdf', '2024-01-01', '2024-12-31')


async def test_limit_out_of_range_is_rejected():
    err, msg = await _call('docvault_search', {'query': 'x', 'limit': 500})
    assert err and 'limit' in msg


# ── docvault_get_document ────────────────────────────────────────────────────

TASK = {'file_hash': 'h1', 'file_path': 'Z:\\Documents\\Bills\\invoice-june.pdf', 'file_type': 'pdf',
        'status': 'COMPLETED', 'file_size': 5555, 'file_created': '2024-06-01', 'file_modified': '2024-06-02',
        'error_log': None, 'metadata_json': None, 'worker_id': None}
TEXT = 'abcdefghij' * 5   # 50 chars


async def test_get_document_by_id_returns_metadata_and_text(api):
    api.on('GET', '/api/catalog/h1', body=TASK)
    api.on('GET', '/api/catalog/h1/text', body={'extracted_text': TEXT})

    err, out = await _call('docvault_get_document', {'document_id': 'h1'})

    assert not err
    assert out == {'file_hash': 'h1', 'file_name': 'invoice-june.pdf', 'file_path': TASK['file_path'],
                   'file_type': 'pdf', 'file_size': 5555, 'file_modified': '2024-06-02',
                   'status': 'COMPLETED', 'total_chars': 50, 'offset': 0, 'text': TEXT, 'next_offset': None}


async def test_get_document_pages_through_long_text(api):
    api.on('GET', '/api/catalog/h1', body=TASK)
    api.on('GET', '/api/catalog/h1/text', body={'extracted_text': TEXT})

    _, first = await _call('docvault_get_document', {'document_id': 'h1', 'max_chars': 20})
    _, last = await _call('docvault_get_document', {'document_id': 'h1', 'offset': 40, 'max_chars': 20})

    assert (first['text'], first['next_offset']) == (TEXT[:20], 20)
    assert (last['text'], last['next_offset']) == (TEXT[40:], None)


async def test_get_document_by_path_resolves_the_hash(api):
    api.on('GET', '/api/catalog/inspect', body={'task': TASK, 'chunks': [], 'images': []})
    api.on('GET', '/api/catalog/h1', body=TASK)
    api.on('GET', '/api/catalog/h1/text', body={'extracted_text': TEXT})

    err, out = await _call('docvault_get_document', {'file_path': TASK['file_path']})

    assert not err and out['file_hash'] == 'h1'
    assert api.last('/api/catalog/inspect').url.params['path'] == TASK['file_path']


async def test_get_document_needs_exactly_one_identifier():
    err, msg = await _call('docvault_get_document', {})
    assert err and 'document_id' in msg and 'file_path' in msg
    err, msg = await _call('docvault_get_document', {'document_id': 'h1', 'file_path': 'x'})
    assert err and 'not both' in msg


async def test_unknown_document_is_a_readable_error(api):
    api.on('GET', '/api/catalog/zzz', status=404, body={'detail': 'File not found'})
    err, msg = await _call('docvault_get_document', {'document_id': 'zzz'})
    assert err and "No document with id 'zzz'" in msg


async def test_unextracted_document_reports_its_status(api):
    api.on('GET', '/api/catalog/h1', body={**TASK, 'status': 'ERROR', 'error_log': 'Corrupt PDF'})
    api.on('GET', '/api/catalog/h1/text', status=404, body={'detail': 'Extracted text not found'})
    err, msg = await _call('docvault_get_document', {'document_id': 'h1'})
    assert err and 'status ERROR' in msg and 'Corrupt PDF' in msg


# ── docvault_status ──────────────────────────────────────────────────────────

async def test_status_summarizes_documents_vaults_workers_and_health(api):
    api.on('GET', '/api/utils/health', body={
        'counts': {'COMPLETED': 10, 'ERROR': 2, 'MISSING': 1},
        'vector_store': {'ok': True, 'points': 99},
        'issues': [{'title': '2 other extraction errors', 'severity': 'info', 'detail': 'long text'}]})
    api.on('GET', '/api/workers/status', body={'paused': False, 'stalled': False,
                                               'stall_minutes': 0, 'embedding_progress': None})

    err, out = await _call('docvault_status', {})

    assert not err
    assert out['documents'] == {'total': 13, 'by_status': {'COMPLETED': 10, 'ERROR': 2, 'MISSING': 1}}
    assert out['vaults'] == [
        {'name': 'Documents', 'vault_id': 'v-docs', 'folder': 'Z:/Documents', 'state': 'active'},
        {'name': 'Mail Archive', 'vault_id': 'v-mail', 'folder': 'Z:/Mail', 'state': 'archived'},
    ]
    assert out['workers'] == {'paused': False, 'stalled': False}
    assert out['search_index'] == {'ok': True, 'chunks': 99}
    assert out['issues'] == ['2 other extraction errors']


# ── docvault_ask ─────────────────────────────────────────────────────────────

async def test_ask_returns_answer_and_sources(api):
    api.on('POST', '/api/query', body={
        'answer': 'You paid $120.', 'thinking': 'hidden',
        'sources': [{'file_hash': 'h1', 'file_path': 'Z:\\Documents\\Bills\\invoice-june.pdf',
                     'score': 0.8, 'combined_score': 0.01, 'chunk_offset': 1, 'chunk_size': 2,
                     'paragraph_num': 3}]})

    err, out = await _call('docvault_ask', {'question': 'How much was June?', 'vault': 'Documents'},
                           enable_ask=True)

    assert not err
    assert out == {'answer': 'You paid $120.',
                   'sources': [{'file_hash': 'h1', 'file_name': 'invoice-june.pdf',
                                'file_path': 'Z:\\Documents\\Bills\\invoice-june.pdf'}]}
    body = json.loads(api.last('/api/query').content)
    assert body == {'question': 'How much was June?', 'vault_ids': 'v-docs'}


# ── Transport-level errors ───────────────────────────────────────────────────

async def test_server_down_is_a_readable_error(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError('refused', request=request)
    monkeypatch.setattr(docvault_mcp, '_transport', httpx.MockTransport(refuse))
    monkeypatch.setattr(docvault_mcp, 'get_server_url', lambda: 'http://127.0.0.1:8050')

    err, msg = await _call('docvault_status', {})

    assert err
    assert 'DocVault server is not reachable at http://127.0.0.1:8050' in msg


async def test_api_errors_include_status_and_detail(api):
    api.on('GET', '/api/search', status=500, body={'detail': 'boom'})
    err, msg = await _call('docvault_search', {'query': 'x'})
    assert err and '500' in msg and 'boom' in msg


# ── Real stdio process (what Dudeskie actually launches) ─────────────────────

async def test_stdio_handshake_and_tool_listing():
    from mcp.client.stdio import StdioServerParameters
    params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(PROJECT_ROOT, 'mcp_server', 'docvault_mcp.py')],
        cwd=os.path.join(PROJECT_ROOT, 'tests'),   # not the project root: the script must not rely on cwd
        env={**os.environ, 'DOCVAULT_MCP_ENABLE_ASK': '1'},
    )
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == {'docvault_search', 'docvault_get_document', 'docvault_status', 'docvault_ask'}
