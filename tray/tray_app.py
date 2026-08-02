"""DocVault system tray helper."""
import configparser
import os
import traceback
import webbrowser

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
