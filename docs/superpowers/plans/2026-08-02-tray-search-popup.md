# Tray Search Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Search..." item to the DocVault system tray menu that opens a floating, non-modal popup window with a text input, a search-type dropdown (Hybrid/Full-text/Semantic/Filename), and a results pane, backed by the already-running DocVault server's search API.

**Architecture:** `tray/tray_app.py` gains a second always-on participant alongside `pystray`'s existing icon loop: one hidden `tk.Tk()` root running on its own background thread, owning every popup as a `Toplevel` under one Tcl interpreter. The pystray menu callback and popup search calls only ever communicate with the Tk thread through one `queue.Queue`, polled every 100ms via `root.after()` — this avoids both blocking pystray's own thread on network I/O and touching Tk widgets from a non-Tk thread.

**Tech Stack:** Python stdlib `tkinter` (popup UI), `queue`/`threading` (cross-thread messaging), `ctypes` (cursor position via `GetCursorPos`), `httpx` (already a project dependency — HTTP calls to the local FastAPI server).

## Global Constraints

- No new dependency: `tkinter` is stdlib, `httpx>=0.27.0` is already in `requirements.txt`.
- No new file — all changes land in the existing `tray/tray_app.py` (small enough per spec §8).
- No "Ask"/RAG mode in the dropdown — only Hybrid, Full-text, Semantic, Filename (spec §2, §3).
- No LCARS theming — plain native Tk widgets (spec §2).
- No single-instance enforcement — every "Search..." click spawns an independent popup (spec §2).
- Opening a file MUST go through the server's existing `POST /utils/open_path` (vault-root + blocked-extension validation) — never call `os.startfile` directly from the tray process (spec §3, §5).
- Popups are non-modal: never call `grab_set()`. `-topmost` is set once at creation, not continuously reasserted (spec §5).
- Snippet text is truncated to 160 characters (157 + `...`) for display (spec §5, decided during planning).

---

### Task 1: Search type mapping + request URL building

**Files:**
- Modify: `tray/tray_app.py`
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Produces: `SEARCH_TYPES` (list of `(label, mode)` tuples, in dropdown display order: Hybrid, Full-text, Semantic, Filename), `SEARCH_TYPE_LABELS` (dict, label → mode), `build_search_request(base_url, query, mode) -> (url, params)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tray_app.py`:

```python
from tray.tray_app import build_search_request, SEARCH_TYPES, SEARCH_TYPE_LABELS


def test_search_types_are_ordered_hybrid_fulltext_semantic_filename():
    assert [label for label, _ in SEARCH_TYPES] == ['Hybrid', 'Full-text', 'Semantic', 'Filename']
    assert SEARCH_TYPE_LABELS == {'Hybrid': 'hybrid', 'Full-text': 'fts', 'Semantic': 'semantic', 'Filename': 'filename'}


def test_build_search_request_filename_mode():
    url, params = build_search_request('http://127.0.0.1:8050', 'invoice', 'filename')
    assert url == 'http://127.0.0.1:8050/search/filename'
    assert params == {'q': 'invoice'}


def test_build_search_request_content_modes():
    for mode in ('hybrid', 'fts', 'semantic'):
        url, params = build_search_request('http://127.0.0.1:8050', 'invoice', mode)
        assert url == 'http://127.0.0.1:8050/search'
        assert params == {'q': 'invoice', 'mode': mode}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tray_app.py -k "search_types or build_search_request" -v`
Expected: FAIL with `ImportError` (names don't exist yet).

- [ ] **Step 3: Implement in `tray/tray_app.py`**

Add near the top of the file, after the existing imports and path constants:

```python
SEARCH_TYPES = [
    ('Hybrid', 'hybrid'),
    ('Full-text', 'fts'),
    ('Semantic', 'semantic'),
    ('Filename', 'filename'),
]
SEARCH_TYPE_LABELS = dict(SEARCH_TYPES)


def build_search_request(base_url, query, mode):
    if mode == 'filename':
        return f'{base_url}/search/filename', {'q': query}
    return f'{base_url}/search', {'q': query, 'mode': mode}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tray_app.py -k "search_types or build_search_request" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: add search type mapping and request URL builder to tray helper"
```

---

### Task 2: Pure result-formatting and request/response decision functions

**Files:**
- Modify: `tray/tray_app.py`
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (uses `SEARCH_TYPE_LABELS`).
- Produces: `format_result_summary(result: dict) -> dict` (keys `filename`, `path`, `snippet`), `extract_search_request(entry_text: str, mode_label: str) -> tuple[str, str] | None`, `rows_for_response(response: dict) -> tuple[str | None, list[dict]]`, `status_for_open_result(response: dict) -> str | None`. These are consumed by Task 5's widget-touching functions.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tray_app.py`:

```python
from tray.tray_app import (
    format_result_summary, extract_search_request, rows_for_response, status_for_open_result,
)
import os


def test_format_result_summary_truncates_long_snippet():
    result = {'file_path': 'D:\\Vault\\docs\\report.pdf', 'chunk_text': 'x' * 200}
    summary = format_result_summary(result)
    assert summary['filename'] == 'report.pdf'
    assert summary['path'] == 'D:\\Vault\\docs\\report.pdf'
    assert len(summary['snippet']) == 160
    assert summary['snippet'].endswith('...')


def test_format_result_summary_filename_only_result_shows_file_type():
    result = {'file_path': 'D:\\Vault\\docs\\report.pdf', 'file_type': 'pdf'}
    summary = format_result_summary(result)
    assert summary['filename'] == 'report.pdf'
    assert summary['snippet'] == 'pdf'


def test_format_result_summary_missing_path_shows_placeholder():
    summary = format_result_summary({})
    assert summary['filename'] == '(unknown)'
    assert summary['path'] == ''


def test_extract_search_request_strips_and_maps_label():
    assert extract_search_request('  invoice  ', 'Full-text') == ('invoice', 'fts')


def test_extract_search_request_empty_query_returns_none():
    assert extract_search_request('   ', 'Hybrid') is None


def test_extract_search_request_unknown_label_defaults_to_hybrid():
    assert extract_search_request('invoice', 'Nonsense') == ('invoice', 'hybrid')


def test_rows_for_response_error_shows_error_no_rows():
    status, rows = rows_for_response({'error': 'Could not reach DocVault server.', 'results': []})
    assert status == 'Could not reach DocVault server.'
    assert rows == []


def test_rows_for_response_empty_results_shows_no_results():
    status, rows = rows_for_response({'error': None, 'results': []})
    assert status == 'No results.'
    assert rows == []


def test_rows_for_response_degraded_shows_reason_alongside_rows():
    response = {
        'error': None,
        'results': [{'file_path': 'a.txt', 'chunk_text': 'hello'}],
        'degraded': True,
        'degraded_reason': 'Semantic search unavailable',
    }
    status, rows = rows_for_response(response)
    assert status == 'Semantic search unavailable'
    assert len(rows) == 1
    assert rows[0]['filename'] == 'a.txt'


def test_rows_for_response_normal_has_no_status():
    response = {'error': None, 'results': [{'file_path': 'a.txt', 'chunk_text': 'hello'}], 'degraded': False}
    status, rows = rows_for_response(response)
    assert status is None
    assert len(rows) == 1


def test_status_for_open_result_ok_is_none():
    assert status_for_open_result({'status': 'ok'}) is None


def test_status_for_open_result_error_uses_detail():
    assert status_for_open_result({'status': 'error', 'detail': 'Path is outside vault boundaries'}) == 'Path is outside vault boundaries'


def test_status_for_open_result_error_without_detail_has_fallback():
    assert status_for_open_result({'status': 'error'}) == 'Could not open file.'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tray_app.py -k "format_result_summary or extract_search_request or rows_for_response or status_for_open_result" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement in `tray/tray_app.py`**

`import os` is already present in `tray_app.py` — no import change needed for this task. Add:

```python
def format_result_summary(result):
    path = result.get('file_path') or ''
    filename = os.path.basename(path) if path else '(unknown)'
    snippet = (result.get('chunk_text') or '').strip().replace('\n', ' ')
    if snippet:
        if len(snippet) > 160:
            snippet = snippet[:157] + '...'
    else:
        snippet = result.get('file_type') or ''
    return {'filename': filename, 'path': path, 'snippet': snippet}


def extract_search_request(entry_text, mode_label):
    query = (entry_text or '').strip()
    if not query:
        return None
    mode = SEARCH_TYPE_LABELS.get(mode_label, 'hybrid')
    return query, mode


def rows_for_response(response):
    if response.get('error'):
        return response['error'], []
    rows = [format_result_summary(r) for r in (response.get('results') or [])]
    if not rows:
        return 'No results.', []
    status_text = response.get('degraded_reason') if response.get('degraded') else None
    return status_text, rows


def status_for_open_result(response):
    if response.get('status') == 'ok':
        return None
    return response.get('detail') or 'Could not open file.'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tray_app.py -k "format_result_summary or extract_search_request or rows_for_response or status_for_open_result" -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: add pure result-formatting and response-decision helpers to tray helper"
```

---

### Task 3: HTTP calls to the DocVault server (search + open file)

**Files:**
- Modify: `tray/tray_app.py`
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Consumes: `build_search_request` (Task 1).
- Produces: `perform_search(base_url, query, mode, timeout=10) -> dict` (keys `results`, `error`, `degraded`, `degraded_reason`), `open_result(base_url, file_path, timeout=10) -> dict` (server's raw `{"status": ..., "detail": ...}` shape). Consumed by Task 5's `_search_worker`/`_open_worker`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tray_app.py` (also add `import httpx` and `from unittest.mock import patch` at the top if not already imported — `patch` and `MagicMock` are already imported; add `import httpx`):

```python
import httpx
from tray.tray_app import perform_search, open_result


@patch('tray.tray_app.httpx.get')
def test_perform_search_content_mode_success(mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {
        'results': [{'file_path': 'a.txt', 'chunk_text': 'hello'}],
        'degraded': False,
        'degraded_reason': '',
    })
    response = perform_search('http://127.0.0.1:8050', 'invoice', 'hybrid')
    assert response['error'] is None
    assert response['results'][0]['file_path'] == 'a.txt'
    mock_get.assert_called_once_with(
        'http://127.0.0.1:8050/search', params={'q': 'invoice', 'mode': 'hybrid'}, timeout=10,
    )


@patch('tray.tray_app.httpx.get')
def test_perform_search_filename_mode_handles_plain_list_response(mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: [{'file_path': 'a.txt'}])
    response = perform_search('http://127.0.0.1:8050', 'invoice', 'filename')
    assert response['results'] == [{'file_path': 'a.txt'}]
    assert response['error'] is None


@patch('tray.tray_app.httpx.get', side_effect=httpx.ConnectError('refused'))
def test_perform_search_connection_error_returns_friendly_message(mock_get):
    response = perform_search('http://127.0.0.1:8050', 'invoice', 'hybrid')
    assert response['error'] == 'Could not reach DocVault server.'
    assert response['results'] == []


@patch('tray.tray_app.httpx.get')
def test_perform_search_non_200_status_returns_friendly_message(mock_get):
    mock_get.return_value = MagicMock(status_code=500)
    response = perform_search('http://127.0.0.1:8050', 'invoice', 'hybrid')
    assert response['error'] == 'Could not reach DocVault server.'


@patch('tray.tray_app.httpx.post')
def test_open_result_success_returns_server_response(mock_post):
    mock_post.return_value = MagicMock(json=lambda: {'status': 'ok'})
    response = open_result('http://127.0.0.1:8050', 'D:\\Vault\\a.txt')
    assert response == {'status': 'ok'}
    mock_post.assert_called_once_with(
        'http://127.0.0.1:8050/utils/open_path',
        json={'path': 'D:\\Vault\\a.txt', 'action': 'file'}, timeout=10,
    )


@patch('tray.tray_app.httpx.post', side_effect=httpx.ConnectError('refused'))
def test_open_result_connection_error_returns_friendly_message(mock_post):
    response = open_result('http://127.0.0.1:8050', 'D:\\Vault\\a.txt')
    assert response == {'status': 'error', 'detail': 'Could not reach DocVault server.'}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tray_app.py -k "perform_search or open_result" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement in `tray/tray_app.py`**

Add `import httpx` to the top imports. Then add:

```python
def perform_search(base_url, query, mode, timeout=10):
    url, params = build_search_request(base_url, query, mode)
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
    except httpx.HTTPError:
        return {'results': [], 'error': 'Could not reach DocVault server.', 'degraded': False, 'degraded_reason': ''}
    if resp.status_code != 200:
        return {'results': [], 'error': 'Could not reach DocVault server.', 'degraded': False, 'degraded_reason': ''}
    data = resp.json()
    if isinstance(data, list):
        return {'results': data, 'error': None, 'degraded': False, 'degraded_reason': ''}
    return {
        'results': data.get('results', []),
        'error': None,
        'degraded': data.get('degraded', False),
        'degraded_reason': data.get('degraded_reason', ''),
    }


def open_result(base_url, file_path, timeout=10):
    try:
        resp = httpx.post(
            f'{base_url}/utils/open_path', json={'path': file_path, 'action': 'file'}, timeout=timeout,
        )
    except httpx.HTTPError:
        return {'status': 'error', 'detail': 'Could not reach DocVault server.'}
    try:
        return resp.json()
    except ValueError:
        return {'status': 'error', 'detail': 'Could not reach DocVault server.'}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tray_app.py -k "perform_search or open_result" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: add HTTP search and open-file calls to tray helper"
```

---

### Task 4: Cursor position helper, module-level popup state, and "Search..." menu item

**Files:**
- Modify: `tray/tray_app.py`
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Produces: `get_cursor_pos() -> (int, int)`, module-level `_popup_queue: queue.Queue`, `_popups: dict`, `_popup_id_seq` (an `itertools.count(1)`), `open_search_popup(icon=None, item=None)` (menu callback — puts `('spawn', x, y)` onto `_popup_queue`). Modifies `build_menu()` to add a `'Search...'` item. Consumed by Task 5 (`_popups`, `_popup_id_seq`) and Task 6 (`_popup_queue`, `open_search_popup` wired into the running menu).
- Replaces the existing `test_build_menu_has_open_and_exit_items` test (menu now has 3 items, not 2).

- [ ] **Step 1: Write the failing tests**

In `tests/test_tray_app.py`, add `import queue` and `import tray.tray_app as tray_app` to the top imports, then replace the existing test:

```python
def test_build_menu_has_open_and_exit_items():
    menu = build_menu()
    items = list(menu)
    assert len(items) == 2
    assert items[0].text == 'Open DocVault'
    assert items[0].default is True
    assert items[1].text == 'Exit'
```

with:

```python
def test_build_menu_has_open_search_and_exit_items():
    menu = build_menu()
    items = list(menu)
    assert len(items) == 3
    assert items[0].text == 'Open DocVault'
    assert items[0].default is True
    assert items[1].text == 'Search...'
    assert items[2].text == 'Exit'
```

And add:

```python
from tray.tray_app import get_cursor_pos, open_search_popup


def test_get_cursor_pos_returns_two_ints():
    x, y = get_cursor_pos()
    assert isinstance(x, int)
    assert isinstance(y, int)


def test_open_search_popup_queues_spawn_message_with_cursor_pos(monkeypatch):
    test_queue = queue.Queue()
    monkeypatch.setattr(tray_app, '_popup_queue', test_queue)
    monkeypatch.setattr(tray_app, 'get_cursor_pos', lambda: (100, 200))
    tray_app.open_search_popup()
    assert test_queue.get_nowait() == ('spawn', 100, 200)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tray_app.py -k "menu or cursor_pos or open_search_popup" -v`
Expected: FAIL — the renamed menu test's old counterpart is gone (that's expected, it's being replaced), the new menu test fails on item count, `get_cursor_pos`/`open_search_popup` fail with `ImportError`.

- [ ] **Step 3: Implement in `tray/tray_app.py`**

Add imports at the top:

```python
import ctypes
import ctypes.wintypes
import itertools
import queue
```

Add module-level state and helpers (after the `SEARCH_TYPE_LABELS` block from Task 1):

```python
_popup_queue = queue.Queue()
_popups = {}
_popup_id_seq = itertools.count(1)


def get_cursor_pos():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def open_search_popup(icon=None, item=None):
    x, y = get_cursor_pos()
    _popup_queue.put(('spawn', x, y))
```

Update `build_menu()`:

```python
def build_menu():
    return pystray.Menu(
        pystray.MenuItem('Open DocVault', open_docvault, default=True),
        pystray.MenuItem('Search...', open_search_popup),
        pystray.MenuItem('Exit', exit_tray),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tray_app.py -k "menu or cursor_pos or open_search_popup" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full test file to confirm nothing else broke**

Run: `pytest tests/test_tray_app.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: add Search menu item, cursor position helper, and popup queue state"
```

---

### Task 5: Popup window creation and result/status rendering

**Files:**
- Modify: `tray/tray_app.py`
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Consumes: `get_server_url` (existing), `extract_search_request`, `rows_for_response`, `status_for_open_result` (Task 2), `perform_search`, `open_result` (Task 3), `_popups`, `_popup_id_seq`, `_popup_queue` (Task 4).
- Produces: `create_popup(x, y) -> int` (popup id; builds the `Toplevel` and registers its widget state into `_popups`), `update_results_for_popup(popup_id, response)`, `apply_open_result_status(popup_id, response)`, `_search_worker(popup_id, base_url, query, mode)`, `_open_worker(popup_id, base_url, file_path)`. Consumed by Task 6's `handle_message`.

This is the first task touching real `tkinter` widgets. Tests create a real (withdrawn) `tk.Tk()` root directly — this works because development happens on an interactive Windows desktop session, not headless CI.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tray_app.py`:

```python
import tkinter as tk


def test_create_popup_registers_state(monkeypatch):
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr(tray_app, '_tk_root', root)
    monkeypatch.setattr(tray_app, 'get_server_url', lambda: 'http://127.0.0.1:8050')
    popup_id = tray_app.create_popup(50, 60)
    try:
        assert popup_id in tray_app._popups
        state = tray_app._popups[popup_id]
        assert state['base_url'] == 'http://127.0.0.1:8050'
        assert isinstance(state['window'], tk.Toplevel)
    finally:
        tray_app._popups[popup_id]['window'].destroy()
        tray_app._popups.clear()
        root.destroy()


def test_update_results_for_popup_renders_rows(monkeypatch):
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr(tray_app, '_tk_root', root)
    monkeypatch.setattr(tray_app, 'get_server_url', lambda: 'http://127.0.0.1:8050')
    popup_id = tray_app.create_popup(50, 60)
    try:
        tray_app.update_results_for_popup(popup_id, {
            'error': None, 'results': [{'file_path': 'a.txt', 'chunk_text': 'hello world'}], 'degraded': False,
        })
        state = tray_app._popups[popup_id]
        assert state['status_label'].cget('text') == ''
        assert len(state['results_frame'].winfo_children()) == 1
    finally:
        tray_app._popups[popup_id]['window'].destroy()
        tray_app._popups.clear()
        root.destroy()


def test_update_results_for_popup_shows_no_results_status(monkeypatch):
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr(tray_app, '_tk_root', root)
    monkeypatch.setattr(tray_app, 'get_server_url', lambda: 'http://127.0.0.1:8050')
    popup_id = tray_app.create_popup(50, 60)
    try:
        tray_app.update_results_for_popup(popup_id, {'error': None, 'results': []})
        state = tray_app._popups[popup_id]
        assert state['status_label'].cget('text') == 'No results.'
        assert len(state['results_frame'].winfo_children()) == 0
    finally:
        tray_app._popups[popup_id]['window'].destroy()
        tray_app._popups.clear()
        root.destroy()


def test_update_results_for_popup_ignores_already_closed_popup():
    tray_app.update_results_for_popup(999999, {'error': None, 'results': []})  # must not raise


def test_apply_open_result_status_sets_error_text(monkeypatch):
    root = tk.Tk()
    root.withdraw()
    monkeypatch.setattr(tray_app, '_tk_root', root)
    monkeypatch.setattr(tray_app, 'get_server_url', lambda: 'http://127.0.0.1:8050')
    popup_id = tray_app.create_popup(50, 60)
    try:
        tray_app.apply_open_result_status(popup_id, {'status': 'error', 'detail': 'Path is outside vault boundaries'})
        state = tray_app._popups[popup_id]
        assert state['status_label'].cget('text') == 'Path is outside vault boundaries'
    finally:
        tray_app._popups[popup_id]['window'].destroy()
        tray_app._popups.clear()
        root.destroy()


@patch('tray.tray_app.perform_search')
def test_search_worker_puts_results_message_on_queue(mock_perform):
    mock_perform.return_value = {'results': [], 'error': None, 'degraded': False, 'degraded_reason': ''}
    test_queue = queue.Queue()
    with patch.object(tray_app, '_popup_queue', test_queue):
        tray_app._search_worker(1, 'http://127.0.0.1:8050', 'invoice', 'hybrid')
    kind, popup_id, response = test_queue.get_nowait()
    assert (kind, popup_id) == ('results', 1)
    mock_perform.assert_called_once_with('http://127.0.0.1:8050', 'invoice', 'hybrid')


@patch('tray.tray_app.open_result')
def test_open_worker_puts_open_result_done_message_on_queue(mock_open):
    mock_open.return_value = {'status': 'ok'}
    test_queue = queue.Queue()
    with patch.object(tray_app, '_popup_queue', test_queue):
        tray_app._open_worker(1, 'http://127.0.0.1:8050', 'D:\\Vault\\a.txt')
    assert test_queue.get_nowait() == ('open_result_done', 1, {'status': 'ok'})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tray_app.py -k "create_popup or update_results_for_popup or apply_open_result_status or search_worker or open_worker" -v`
Expected: FAIL with `AttributeError`/`ImportError` (functions and `_tk_root` don't exist yet).

- [ ] **Step 3: Implement in `tray/tray_app.py`**

Add imports at the top: `import threading`, `import tkinter as tk`, `from tkinter import ttk`.

Add module-level `_tk_root = None` near the other module state (`_popup_queue`, `_popups`, `_popup_id_seq`).

Add:

```python
def create_popup(x, y):
    popup_id = next(_popup_id_seq)
    win = tk.Toplevel(_tk_root)
    win.title('DocVault Search')
    win.geometry(f'420x360+{x}+{y}')
    win.attributes('-topmost', True)

    entry = tk.Entry(win)
    entry.pack(fill='x', padx=8, pady=(8, 4))
    entry.focus_set()

    mode_var = tk.StringVar(value='Hybrid')
    mode_combo = ttk.Combobox(
        win, textvariable=mode_var, state='readonly', values=[label for label, _ in SEARCH_TYPES],
    )
    mode_combo.pack(fill='x', padx=8, pady=4)

    button_row = tk.Frame(win)
    button_row.pack(fill='x', padx=8)
    search_btn = tk.Button(button_row, text='Search')
    search_btn.pack(side='left')

    status_label = tk.Label(win, text='', anchor='w', fg='#666666')
    status_label.pack(fill='x', padx=8, pady=(4, 0))

    canvas = tk.Canvas(win, borderwidth=0)
    scrollbar = tk.Scrollbar(win, orient='vertical', command=canvas.yview)
    results_frame = tk.Frame(canvas)
    results_frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0, 0), window=results_frame, anchor='nw')
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side='left', fill='both', expand=True, padx=(8, 0), pady=8)
    scrollbar.pack(side='right', fill='y', pady=8)

    base_url = get_server_url()
    _popups[popup_id] = {
        'window': win,
        'status_label': status_label,
        'results_frame': results_frame,
        'canvas': canvas,
        'base_url': base_url,
    }

    def run_search(event=None):
        request = extract_search_request(entry.get(), mode_var.get())
        if request is None:
            return
        query, mode = request
        status_label.config(text='Searching...')
        threading.Thread(target=_search_worker, args=(popup_id, base_url, query, mode), daemon=True).start()

    entry.bind('<Return>', run_search)
    search_btn.config(command=run_search)

    def on_close():
        _popups.pop(popup_id, None)
        win.destroy()

    win.protocol('WM_DELETE_WINDOW', on_close)
    return popup_id


def update_results_for_popup(popup_id, response):
    state = _popups.get(popup_id)
    if state is None:
        return
    status_text, rows = rows_for_response(response)
    state['status_label'].config(text=status_text or '')

    for child in state['results_frame'].winfo_children():
        child.destroy()

    for row in rows:
        row_frame = tk.Frame(state['results_frame'], cursor='hand2')
        row_frame.pack(fill='x', pady=2)
        name_label = tk.Label(row_frame, text=row['filename'], font=('TkDefaultFont', 9, 'bold'), anchor='w')
        name_label.pack(fill='x')
        path_label = tk.Label(row_frame, text=row['path'], fg='#888888', anchor='w')
        path_label.pack(fill='x')
        snippet_label = tk.Label(row_frame, text=row['snippet'], anchor='w', wraplength=380, justify='left')
        snippet_label.pack(fill='x')

        def on_double_click(event, path=row['path']):
            _handle_result_double_click(popup_id, path)

        for widget in (row_frame, name_label, path_label, snippet_label):
            widget.bind('<Double-Button-1>', on_double_click)


def apply_open_result_status(popup_id, response):
    state = _popups.get(popup_id)
    if state is None:
        return
    state['status_label'].config(text=status_for_open_result(response) or '')


def _search_worker(popup_id, base_url, query, mode):
    response = perform_search(base_url, query, mode)
    _popup_queue.put(('results', popup_id, response))


def _handle_result_double_click(popup_id, path):
    state = _popups.get(popup_id)
    if state is None:
        return
    state['status_label'].config(text='Opening...')
    threading.Thread(target=_open_worker, args=(popup_id, state['base_url'], path), daemon=True).start()


def _open_worker(popup_id, base_url, path):
    response = open_result(base_url, path)
    _popup_queue.put(('open_result_done', popup_id, response))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tray_app.py -k "create_popup or update_results_for_popup or apply_open_result_status or search_worker or open_worker" -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full test file to confirm nothing else broke**

Run: `pytest tests/test_tray_app.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: add popup window creation and result/status rendering to tray helper"
```

---

### Task 6: Message dispatch, hidden Tk host thread, and end-to-end wiring

**Files:**
- Modify: `tray/tray_app.py`
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Consumes: `create_popup`, `update_results_for_popup`, `apply_open_result_status` (Task 5), `_popup_queue`, `_tk_root` (Tasks 4/5), `open_search_popup` (Task 4, already wired into `build_menu()`).
- Produces: `handle_message(msg)`, `_drain_queue()`, `start_popup_host()`. Modifies `main()` to start the Tk host thread before running the pystray icon loop.

`_drain_queue`/`start_popup_host`/`main()`'s threading wiring are not unit tested — they require a live Tk event loop and a running pystray icon, which is GUI/OS-level territory the rest of this project's test suite doesn't cover either (per the prior tray feature's status doc). This task's actual deliverable is verified manually per spec §7.

- [ ] **Step 1: Write the failing tests for `handle_message`**

Add to `tests/test_tray_app.py`:

```python
def test_handle_message_spawn_calls_create_popup(monkeypatch):
    calls = []
    monkeypatch.setattr(tray_app, 'create_popup', lambda x, y: calls.append((x, y)))
    tray_app.handle_message(('spawn', 10, 20))
    assert calls == [(10, 20)]


def test_handle_message_results_calls_update_results_for_popup(monkeypatch):
    calls = []
    monkeypatch.setattr(tray_app, 'update_results_for_popup', lambda pid, resp: calls.append((pid, resp)))
    tray_app.handle_message(('results', 1, {'error': None, 'results': []}))
    assert calls == [(1, {'error': None, 'results': []})]


def test_handle_message_open_result_done_calls_apply_open_result_status(monkeypatch):
    calls = []
    monkeypatch.setattr(tray_app, 'apply_open_result_status', lambda pid, resp: calls.append((pid, resp)))
    tray_app.handle_message(('open_result_done', 1, {'status': 'ok'}))
    assert calls == [(1, {'status': 'ok'})]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tray_app.py -k "handle_message" -v`
Expected: FAIL with `AttributeError` (`handle_message` doesn't exist yet).

- [ ] **Step 3: Implement `handle_message`, `_drain_queue`, `start_popup_host`, and update `main()`**

Add:

```python
def handle_message(msg):
    kind = msg[0]
    if kind == 'spawn':
        _, x, y = msg
        create_popup(x, y)
    elif kind == 'results':
        _, popup_id, response = msg
        update_results_for_popup(popup_id, response)
    elif kind == 'open_result_done':
        _, popup_id, response = msg
        apply_open_result_status(popup_id, response)


def _drain_queue():
    try:
        while True:
            handle_message(_popup_queue.get_nowait())
    except queue.Empty:
        pass
    _tk_root.after(100, _drain_queue)


def start_popup_host():
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()
    _tk_root.after(100, _drain_queue)
    _tk_root.mainloop()
```

Update `main()`:

```python
def main():
    try:
        threading.Thread(target=start_popup_host, daemon=True).start()
        image = Image.open(ICON_PATH)
        icon = pystray.Icon('DocVault', image, 'DocVault', build_menu())
        icon.run()
    except Exception:
        with open(LOG_PATH, 'a') as f:
            f.write(f'--- tray_app failed to start ---\n')
            f.write(traceback.format_exc())
            f.write('\n')
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tray_app.py -k "handle_message" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full test file**

Run: `pytest tests/test_tray_app.py -v`
Expected: all PASS.

- [ ] **Step 6: Manual end-to-end verification** (per spec §7 — no automated test covers this layer)

1. Run `python tray/tray_app.py` (or `pythonw tray/tray_app.py`) with the DocVault server already running.
2. Right-click the tray icon → "Search..." — a popup appears near the cursor, and you can still click/focus other windows while it's open (non-modal).
3. Open a second popup while the first is still open — both work independently.
4. Type a query, try each dropdown mode (Hybrid, Full-text, Semantic, Filename) against real vault content — results appear in the pane.
5. Double-click a result — the file opens in its default app.
6. Stop the DocVault server, search again — an inline "Could not reach DocVault server." message appears, no crash.
7. Submit an empty query — nothing happens, no error.

- [ ] **Step 7: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: wire up tray search popup message dispatch and Tk host thread"
```
