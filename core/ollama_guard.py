"""
Ollama self-heal guard.

Relaunches a *local* Ollama server that has died, so RAG/embeddings recover on their own.

Why this exists: the Ollama tray app auto-updates itself with /FORCECLOSEAPPLICATIONS, which
kills every ollama.exe in its install dir — including the `ollama serve` that start.ps1
launched on our configured port (11600) — and then relaunches only its own server on
0.0.0.0:11434, ignoring our OLLAMA_HOST. start.ps1 only checks Ollama once at startup, so
nothing brought the configured port back and every RAG query failed with WinError 10061.

Policy (kept conservative — killing a healthy Ollama would be worse than the outage):
  * only acts when the configured host is local and llm:provider is 'ollama'
  * only a *refused* connection counts as dead; timeouts mean busy, never restarted
  * needs N consecutive refusals, then a cooldown between restart attempts
  * relaunches bound to 127.0.0.1 on the configured port (never 0.0.0.0)
"""
import os
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse

from core import logger

DEFAULT_OLLAMA_PORT = 11434
_LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')


def parse_ollama_host(url: str) -> tuple[str, int]:
    """'http://localhost:11600' -> ('localhost', 11600); port defaults to 11434."""
    url = (url or '').strip()
    if '://' not in url:
        url = f'http://{url}'
    parsed = urlparse(url)
    return parsed.hostname or 'localhost', parsed.port or DEFAULT_OLLAMA_PORT


def is_local(host: str) -> bool:
    return host.lower() in _LOCAL_HOSTS


def probe(host: str, port: int, timeout: float = 3.0) -> str:
    """'up' if something accepts the connection, 'refused' if nothing is listening
    (WinError 10061), 'unknown' for anything ambiguous such as a timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 'up'
    except ConnectionRefusedError:
        return 'refused'
    except OSError:
        return 'unknown'


def serve_env(port: int, base: dict | None = None) -> dict:
    """Environment for `ollama serve`: loopback only, on the configured port."""
    env = dict(os.environ if base is None else base)
    env['OLLAMA_HOST'] = f'127.0.0.1:{port}'
    return env


def restart_ollama(port: int, wait_secs: int = 15) -> bool:
    """Stop every ollama* process, then launch `ollama serve` on 127.0.0.1:<port>.

    All ollama processes must go first: the Ollama app holds a single-instance lock, and a
    fresh `ollama serve` silently no-ops while any other instance is alive (same reasoning
    as start.ps1's Start-OllamaOnPort). Returns True once the port accepts connections.
    """
    import psutil
    for proc in psutil.process_iter(['name']):
        if (proc.info.get('name') or '').lower().startswith('ollama'):
            try:
                proc.kill()
            except psutil.Error:
                pass
    time.sleep(0.5)

    exe = shutil.which('ollama') or os.path.join(
        os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Ollama', 'ollama.exe')
    flags = (getattr(subprocess, 'CREATE_NO_WINDOW', 0)
             | getattr(subprocess, 'DETACHED_PROCESS', 0)
             | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    subprocess.Popen(
        [exe, 'serve'], env=serve_env(port), creationflags=flags, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        time.sleep(1)
        if probe('127.0.0.1', port, timeout=1.0) == 'up':
            return True
    return False


class OllamaGuard:
    def __init__(self, settings_get=None, probe=probe, restart=restart_ollama,
                 alert=None, clock=time.monotonic,
                 refusals_before_restart: int = 2, cooldown_secs: int = 300):
        # Collaborators are injected so the policy is testable without real processes.
        # Settings are resolved lazily (never at import time — see project conventions).
        self._settings_get = settings_get
        self._probe = probe
        self._restart = restart
        self._alert = alert
        self._clock = clock
        self._refusals_before_restart = refusals_before_restart
        self._cooldown_secs = cooldown_secs
        self._refusals = 0
        self._last_restart = None

    def _get(self, key):
        if self._settings_get is None:
            from core.settings import settings
            return settings.get(key)
        return self._settings_get(key)

    def _send_alert(self, title, message, level):
        alert = self._alert
        if alert is None:
            from core.alerts import send_alert as alert
        alert(title=title, message=message, level=level, source='ollama_guard')

    def check_once(self) -> str:
        """One probe/decision cycle. Returns what happened:
        skipped | ok | unknown | waiting | cooldown | restarted | restart_failed."""
        enabled = str(self._get('ollama:guard_enabled') or 'true').lower() == 'true'
        if not enabled or self._get('llm:provider') != 'ollama':
            return 'skipped'
        host, port = parse_ollama_host(self._get('ollama:host'))
        if not is_local(host):
            return 'skipped'

        state = self._probe(host, port)
        if state == 'up':
            self._refusals = 0
            return 'ok'
        if state != 'refused':
            return 'unknown'

        self._refusals += 1
        if self._refusals < self._refusals_before_restart:
            return 'waiting'
        now = self._clock()
        if self._last_restart is not None and now - self._last_restart < self._cooldown_secs:
            return 'cooldown'

        self._last_restart = now
        self._refusals = 0
        logger.warn(f"Ollama guard: nothing listening on {host}:{port}, relaunching ollama serve")
        try:
            ok = self._restart(port)
        except Exception as e:
            logger.error(f"Ollama guard: restart raised {e!r}")
            ok = False
        if ok:
            self._send_alert(
                'Ollama restarted',
                f"Ollama was not listening on port {port} (often after an Ollama app "
                f"auto-update). DocVault relaunched it automatically.",
                'warning')
            return 'restarted'
        self._send_alert(
            'Ollama restart failed',
            f"Ollama is not listening on port {port} and the automatic relaunch failed. "
            f"Start it manually (run start.ps1) — RAG and embeddings will fail until then.",
            'error')
        return 'restart_failed'

    def run(self):
        """Daemon loop. Never raises. Sleeps first so it doesn't race start.ps1's own launch."""
        while True:
            try:
                interval = max(5, int(self._get('ollama:guard_interval') or 30))
            except (TypeError, ValueError):
                interval = 30
            time.sleep(interval)
            try:
                self.check_once()
            except Exception as e:
                logger.error(f"Ollama guard: check failed: {e!r}")
