import os

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
