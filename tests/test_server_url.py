import os
import subprocess
import sys

from core.server_url import get_server_url

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _config(tmp_path, text):
    p = tmp_path / 'config.ini'
    p.write_text(text, encoding='utf-8')
    return str(p)


def test_reads_host_and_port_from_config(tmp_path):
    assert get_server_url(_config(tmp_path, '[server]\nhost = 0.0.0.0\nport = 9999\n')) == 'http://0.0.0.0:9999'


def test_defaults_when_section_missing(tmp_path):
    assert get_server_url(_config(tmp_path, '[other]\nx = 1\n')) == 'http://127.0.0.1:8050'


def test_defaults_when_file_missing(tmp_path):
    assert get_server_url(str(tmp_path / 'nope.ini')) == 'http://127.0.0.1:8050'


def test_import_pulls_in_nothing_that_writes_to_stdout():
    # The MCP stdio server imports this module; stdout is its JSON-RPC channel,
    # so it must not drag in core.settings/core.logger (which print on use/import).
    code = ("import sys; import core.server_url; "
            "bad = [m for m in ('core.settings', 'core.logger', 'core.manager') if m in sys.modules]; "
            "print(','.join(bad))")
    out = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ''
