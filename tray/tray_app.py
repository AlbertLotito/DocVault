"""DocVault system tray helper."""
import configparser
import os
import traceback
import webbrowser

import httpx
import pystray
from PIL import Image

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.ini')
LOG_PATH = os.path.join(PROJECT_ROOT, 'tray_app_error.log')

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


def perform_search(base_url, query, mode, timeout=10):
    url, params = build_search_request(base_url, query, mode)
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
    except httpx.HTTPError:
        return {'results': [], 'error': 'Could not reach DocVault server.', 'degraded': False, 'degraded_reason': ''}
    if resp.status_code != 200:
        return {'results': [], 'error': 'Could not reach DocVault server.', 'degraded': False, 'degraded_reason': ''}
    try:
        data = resp.json()
    except ValueError:
        return {'results': [], 'error': 'Could not reach DocVault server.', 'degraded': False, 'degraded_reason': ''}
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


def get_server_url(config_path=CONFIG_PATH):
    """Read [server] host/port from config.ini and build the base URL."""
    parser = configparser.ConfigParser()
    parser.read(config_path)
    host = parser.get('server', 'host', fallback='127.0.0.1')
    port = parser.get('server', 'port', fallback='8050')
    return f'http://{host}:{port}'


def open_docvault(icon=None, item=None):
    webbrowser.open(get_server_url())


def exit_tray(icon, item):
    icon.stop()


def build_menu():
    return pystray.Menu(
        pystray.MenuItem('Open DocVault', open_docvault, default=True),
        pystray.MenuItem('Exit', exit_tray),
    )


ICON_PATH = os.path.join(PROJECT_ROOT, 'frontend', 'static', 'tray_icon.ico')


def main():
    try:
        image = Image.open(ICON_PATH)
        icon = pystray.Icon('DocVault', image, 'DocVault', build_menu())
        icon.run()
    except Exception:
        with open(LOG_PATH, 'a') as f:
            f.write(f'--- tray_app failed to start ---\n')
            f.write(traceback.format_exc())
            f.write('\n')
        raise


if __name__ == '__main__':
    main()
