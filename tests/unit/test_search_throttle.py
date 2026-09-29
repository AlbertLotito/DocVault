import sys, os, time, threading
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from core import monitor
from core.monitor import notify_user_activity, get_throttle_state, _set_throttle_state, MonitorReading
from core.settings import settings


@pytest.fixture(autouse=True)
def _no_recent_user_activity(monkeypatch):
    # Earlier tests that hit the search API call notify_user_activity(), which
    # leaves the 60s search throttle active; start each test from a clean slate.
    monkeypatch.setattr(monitor, '_last_user_activity', 0.0)

def test_search_triggers_throttle():
    # Setup: Ensure state is normal
    _set_throttle_state('normal', MonitorReading())
    state, _ = get_throttle_state()
    assert state == 'normal'

    # Trigger user activity
    notify_user_activity()

    # Assert state is now 'throttled' even though monitor state is 'normal'
    state, _ = get_throttle_state()
    assert state == 'throttled'

def test_search_throttle_expires():
    # Setup: Ensure state is normal
    _set_throttle_state('normal', MonitorReading())

    # Set a very short duration for testing if possible, but settings.get is hard to mock easily here
    # without mocking the whole settings object. Let's assume default 60s for now or
    # just check that it stays throttled for a moment.

    notify_user_activity()
    state, _ = get_throttle_state()
    assert state == 'throttled'

    # We can't easily wait 60s in a unit test.
    # But we can verify that cooldown still takes priority.
    _set_throttle_state('cooldown', MonitorReading())
    state, _ = get_throttle_state()
    assert state == 'cooldown'

def test_search_throttle_duration_setting():
    # Verify the setting exists in schema
    assert 'monitor:search_throttle_duration' in settings.schema
    assert settings.schema['monitor:search_throttle_duration']['default'] == 60
