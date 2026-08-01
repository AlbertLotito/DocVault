import os
from unittest.mock import MagicMock, patch

from tray.tray_app import get_server_url


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


def test_build_menu_has_open_and_exit_items():
    menu = build_menu()
    items = list(menu)
    assert len(items) == 2
    assert items[0].text == 'Open DocVault'
    assert items[0].default is True
    assert items[1].text == 'Exit'


@patch('tray.tray_app.webbrowser.open')
@patch('tray.tray_app.get_server_url', return_value='http://127.0.0.1:8050')
def test_open_docvault_opens_browser_to_server_url(mock_get_url, mock_open):
    open_docvault()
    mock_open.assert_called_once_with('http://127.0.0.1:8050')


def test_exit_tray_stops_icon_without_touching_server():
    fake_icon = MagicMock()
    exit_tray(fake_icon, None)
    fake_icon.stop.assert_called_once()
