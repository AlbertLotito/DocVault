"""
Tests for core/ollama_guard.py — the self-heal guard that relaunches a dead local Ollama.

Background: on 2026-09-19 the Ollama tray app auto-updated, force-closing the
`ollama serve` that start.ps1 had launched on port 11600. Nothing noticed, so every
RAG query failed with WinError 10061 until someone restarted Ollama by hand.
"""
import socket

from core import ollama_guard
from core.ollama_guard import OllamaGuard, parse_ollama_host, probe, serve_env


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


def make_guard(probe_results, *, settings=None, restart_ok=True):
    """Build a guard whose probe returns `probe_results` in order (last one repeats)."""
    cfg = {
        'llm:provider': 'ollama',
        'ollama:host': 'http://localhost:11600',
        'ollama:guard_enabled': 'true',
    }
    cfg.update(settings or {})
    results = list(probe_results)
    calls = {'probe': [], 'restart': [], 'alerts': []}

    def fake_probe(host, port):
        calls['probe'].append((host, port))
        return results.pop(0) if len(results) > 1 else results[0]

    def fake_restart(port):
        calls['restart'].append(port)
        return restart_ok

    def fake_alert(title, message, level='info', source='system'):
        calls['alerts'].append({'title': title, 'level': level, 'source': source})

    clock = FakeClock()
    guard = OllamaGuard(
        settings_get=cfg.get, probe=fake_probe, restart=fake_restart,
        alert=fake_alert, clock=clock,
        refusals_before_restart=2, cooldown_secs=300,
    )
    return guard, calls, clock


# --- policy -----------------------------------------------------------------

def test_single_refusal_does_not_restart():
    guard, calls, _ = make_guard(['refused'])
    assert guard.check_once() == 'waiting'
    assert calls['restart'] == []


def test_consecutive_refusals_restart_ollama_on_the_configured_port():
    guard, calls, _ = make_guard(['refused'])
    guard.check_once()
    assert guard.check_once() == 'restarted'
    assert calls['restart'] == [11600]
    assert [a['level'] for a in calls['alerts']] == ['warning']
    assert calls['alerts'][0]['source'] == 'ollama_guard'


def test_a_successful_probe_resets_the_refusal_count():
    guard, calls, _ = make_guard(['refused', 'up', 'refused'])
    guard.check_once()
    assert guard.check_once() == 'ok'
    assert guard.check_once() == 'waiting'
    assert calls['restart'] == []


def test_timeouts_are_never_treated_as_a_dead_server():
    # A busy Ollama (model loading, big batch) times out but is alive — must not be killed.
    guard, calls, _ = make_guard(['unknown'])
    for _ in range(6):
        assert guard.check_once() == 'unknown'
    assert calls['restart'] == []


def test_remote_ollama_host_is_never_restarted():
    guard, calls, _ = make_guard(['refused'], settings={'ollama:host': 'http://192.168.1.50:11434'})
    for _ in range(4):
        assert guard.check_once() == 'skipped'
    assert calls['probe'] == []
    assert calls['restart'] == []


def test_guard_does_nothing_when_llm_provider_is_not_ollama():
    guard, calls, _ = make_guard(['refused'], settings={'llm:provider': 'claude'})
    assert guard.check_once() == 'skipped'
    assert calls['probe'] == []


def test_guard_can_be_disabled_by_setting():
    guard, calls, _ = make_guard(['refused'], settings={'ollama:guard_enabled': 'false'})
    for _ in range(4):
        assert guard.check_once() == 'skipped'
    assert calls['restart'] == []


def test_cooldown_stops_a_restart_loop_then_allows_another_attempt():
    guard, calls, clock = make_guard(['refused'])
    guard.check_once()
    assert guard.check_once() == 'restarted'          # first restart
    guard.check_once()
    assert guard.check_once() == 'cooldown'           # still refused, but too soon
    assert calls['restart'] == [11600]
    clock.advance(301)
    assert guard.check_once() == 'restarted'          # cooldown elapsed
    assert calls['restart'] == [11600, 11600]


def test_failed_restart_raises_an_error_alert():
    guard, calls, _ = make_guard(['refused'], restart_ok=False)
    guard.check_once()
    assert guard.check_once() == 'restart_failed'
    assert [a['level'] for a in calls['alerts']] == ['error']


def test_restart_exception_is_contained_and_reported():
    guard, calls, _ = make_guard(['refused'])

    def boom(port):
        raise OSError('access denied')
    guard._restart = boom
    guard.check_once()
    assert guard.check_once() == 'restart_failed'
    assert [a['level'] for a in calls['alerts']] == ['error']


# --- helpers (real sockets, no mocks) ---------------------------------------

def test_probe_reports_up_for_a_listening_port():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    try:
        assert probe('127.0.0.1', srv.getsockname()[1]) == 'up'
    finally:
        srv.close()


def test_probe_reports_refused_for_a_port_nothing_listens_on():
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    port = srv.getsockname()[1]
    srv.close()  # port is now free, nothing listening
    assert probe('127.0.0.1', port, timeout=5.0) == 'refused'


def test_parse_ollama_host_handles_url_and_default_port():
    assert parse_ollama_host('http://localhost:11600') == ('localhost', 11600)
    assert parse_ollama_host('http://127.0.0.1:11600/') == ('127.0.0.1', 11600)
    assert parse_ollama_host('http://localhost') == ('localhost', 11434)


def test_serve_env_binds_loopback_on_the_configured_port_only():
    env = serve_env(11600, base={'PATH': 'x', 'OLLAMA_HOST': '0.0.0.0:11434'})
    assert env['OLLAMA_HOST'] == '127.0.0.1:11600'   # never 0.0.0.0 — no LAN exposure
    assert env['PATH'] == 'x'


def test_guard_settings_exist_in_schema_with_safe_defaults():
    from core.settings import settings
    assert str(settings.get('ollama:guard_enabled')).lower() == 'true'
    assert int(settings.get('ollama:guard_interval')) == 30
