"""Base URL of the running DocVault server, for out-of-process clients
(tray app, MCP server).

Deliberately dependency-free: the MCP stdio server imports this, and stdout is
its JSON-RPC channel, so nothing here may import core.settings / core.logger.
"""
import configparser
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.ini')


def get_server_url(config_path=CONFIG_PATH):
    """Read [server] host/port from config.ini and build the base URL."""
    parser = configparser.ConfigParser()
    parser.read(config_path)
    host = parser.get('server', 'host', fallback='127.0.0.1')
    port = parser.get('server', 'port', fallback='8050')
    return f'http://{host}:{port}'
