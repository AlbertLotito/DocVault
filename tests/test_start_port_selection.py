"""
Tests for ollama_port.ps1 (the port-selection helpers dot-sourced by start.ps1).

Regression: start.ps1 used to treat a port as "in use" whenever *anything* was connected to
it - including the healthy Ollama that is supposed to be there. So restarting DocVault
while Ollama was running on 11600 made start.ps1 "pick a new port" (11601), rewrite
config.ini + OLLAMA_HOST, kill Ollama and relaunch it one port higher - and the next
restart moved it again (11602, ...). These tests run the real functions under
Windows PowerShell 5.1 (what the DocVault-Server scheduled task uses) against real sockets.
"""
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PORT_SCRIPT = os.path.join(REPO, 'ollama_port.ps1')


def find_free_ollama_port(preferred: int) -> int:
    """Run the real Find-FreeOllamaPort under powershell.exe and return its result."""
    command = (
        "function Write-Warn { param($m) }; "
        f". '{PORT_SCRIPT}'; "
        f"Find-FreeOllamaPort -Preferred {preferred}"
    )
    out = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return int(out.stdout.strip())


class _FakeOllama(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/version':
            body = json.dumps({'version': '0.0.0-test'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_ollama():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port
    server.shutdown()
    server.server_close()


@pytest.fixture
def non_ollama_listener():
    """Something listening on a port that is NOT Ollama (accepts, then hangs up)."""
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    stop = threading.Event()

    def accept_and_close():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

    threading.Thread(target=accept_and_close, daemon=True).start()
    yield srv.getsockname()[1]
    stop.set()
    srv.close()


def test_keeps_the_preferred_port_when_ollama_is_already_running_on_it(fake_ollama):
    assert find_free_ollama_port(fake_ollama) == fake_ollama


def test_moves_off_a_preferred_port_held_by_something_that_is_not_ollama(non_ollama_listener):
    chosen = find_free_ollama_port(non_ollama_listener)
    assert chosen != non_ollama_listener
    assert 11600 <= chosen <= 11699


def test_keeps_the_preferred_port_when_it_is_free():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()  # nothing listening on it now
    assert find_free_ollama_port(port) == port
