"""DocVault system tray helper."""
import configparser
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.ini')


def get_server_url(config_path=CONFIG_PATH):
    """Read [server] host/port from config.ini and build the base URL."""
    parser = configparser.ConfigParser()
    parser.read(config_path)
    host = parser.get('server', 'host', fallback='127.0.0.1')
    port = parser.get('server', 'port', fallback='8050')
    return f'http://{host}:{port}'
