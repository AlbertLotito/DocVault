import os
import queue
from unittest.mock import MagicMock, patch

import httpx

import tray.tray_app as tray_app
from tray.tray_app import build_search_request, get_server_url, SEARCH_TYPES, SEARCH_TYPE_LABELS


def _write_config(tmp_path, contents):
    config_path = os.path.join(str(tmp_path), 'config.ini')
    with open(config_path, 'w') as f:
        f.write(contents)
    return config_path


def test_get_server_url_reads_config(tmp_path):
    config_path = _write_config(tmp_path, '[server]\nhost = 127.0.0.1\nport = 8050\n')
    assert get_server_url(config_path) == 'http://127.0.0.1:8050'


def test_get_server_url_defaults_when_section_missing(tmp_path):
    config_path = _write_config(tmp_path, '[other]\nkey = value\n')
    assert get_server_url(config_path) == 'http://127.0.0.1:8050'


def test_get_server_url_uses_custom_host_and_port(tmp_path):
    config_path = _write_config(tmp_path, '[server]\nhost = 0.0.0.0\nport = 9999\n')
    assert get_server_url(config_path) == 'http://0.0.0.0:9999'


from tray.tray_app import build_menu, exit_tray, open_docvault


def test_build_menu_has_open_search_and_exit_items():
    menu = build_menu()
    items = list(menu)
    assert len(items) == 3
    assert items[0].text == 'Open DocVault'
    assert items[0].default is True
    assert items[1].text == 'Search...'
    assert items[2].text == 'Exit'


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


@patch('tray.tray_app.webbrowser.open')
@patch('tray.tray_app.get_server_url', return_value='http://127.0.0.1:8050')
def test_open_docvault_opens_browser_to_server_url(mock_get_url, mock_open):
    open_docvault()
    mock_open.assert_called_once_with('http://127.0.0.1:8050')


def test_exit_tray_stops_icon_without_touching_server():
    fake_icon = MagicMock()
    exit_tray(fake_icon, None)
    fake_icon.stop.assert_called_once()


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


from tray.tray_app import (
    format_result_summary, extract_search_request, rows_for_response, status_for_open_result,
)


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


@patch('tray.tray_app.httpx.get')
def test_perform_search_malformed_json_returns_friendly_message(mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=MagicMock(side_effect=ValueError))
    response = perform_search('http://127.0.0.1:8050', 'invoice', 'hybrid')
    assert response['error'] == 'Could not reach DocVault server.'
    assert response['results'] == []


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
