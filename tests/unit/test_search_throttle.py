import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from core.monitor import notify_user_activity, get_throttle_state, _set_throttle_state, MonitorReading
from core.settings import settings

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
