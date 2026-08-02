"""DocVault system tray helper."""
import configparser
import ctypes
import ctypes.wintypes
import itertools
import os
import queue
import threading
import tkinter as tk
import traceback
import webbrowser
from tkinter import ttk

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

_popup_queue = queue.Queue()
_popups = {}
_popup_id_seq = itertools.count(1)
_tk_root = None


def get_cursor_pos():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def open_search_popup(icon=None, item=None):
    x, y = get_cursor_pos()
    _popup_queue.put(('spawn', x, y))


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
        pystray.MenuItem('Search...', open_search_popup),
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
