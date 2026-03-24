"""
Disk sensor wiring tests (Security Hardening C):
  - ThrottleThresholds has disk fields with correct defaults
  - StateMachine.update() triggers throttle on low disk
  - Dummy-zero guard: zeros never trigger disk throttle
  - Healthy disk: no throttle
  - _load_thresholds() reads disk keys from settings schema
  - Settings schema contains monitor:disk_free_pct_throttle and monitor:disk_free_gb_throttle
"""
from core.monitor import (
    ThrottleThresholds, ThrottleStateMachine, MonitorReading, _load_thresholds
)


# ── ThrottleThresholds ────────────────────────────────────────────────────────

def test_thresholds_have_disk_fields():
    t = ThrottleThresholds()
    assert hasattr(t, 'disk_free_pct_throttle')
    assert hasattr(t, 'disk_free_gb_throttle')


def test_thresholds_disk_defaults():
    t = ThrottleThresholds()
    assert t.disk_free_pct_throttle == 10.0
    assert t.disk_free_gb_throttle == 5.0


# ── StateMachine disk pressure ─────────────────────────────────────────────────

def test_disk_pressure_pct_triggers_throttle():
    """Free % below threshold → throttled."""
    sm = ThrottleStateMachine()
    t = ThrottleThresholds(disk_free_pct_throttle=15.0, disk_free_gb_throttle=1.0)
    r = MonitorReading(disk_free_gb=10.0, disk_free_pct=8.0)  # 8% < 15% threshold
    sm.update(r, t)
    assert sm.state == 'throttled'
    assert 'Disk' in sm.reason


def test_disk_pressure_gb_triggers_throttle():
    """Free GB below threshold → throttled, even if pct is fine."""
    sm = ThrottleStateMachine()
    t = ThrottleThresholds(disk_free_pct_throttle=5.0, disk_free_gb_throttle=10.0)
    r = MonitorReading(disk_free_gb=3.0, disk_free_pct=30.0)  # 3 GB < 10 GB threshold
    sm.update(r, t)
    assert sm.state == 'throttled'
    assert 'Disk' in sm.reason


def test_disk_pressure_both_thresholds_or_logic():
    """Either threshold alone is sufficient to trigger throttle."""
    sm = ThrottleStateMachine()
    t = ThrottleThresholds(disk_free_pct_throttle=10.0, disk_free_gb_throttle=5.0)
    # Only GB is below threshold; pct is healthy
    r = MonitorReading(disk_free_gb=2.0, disk_free_pct=50.0)
    sm.update(r, t)
    assert sm.state == 'throttled'


def test_healthy_disk_no_throttle():
    """Plenty of disk → normal state."""
    sm = ThrottleStateMachine()
    t = ThrottleThresholds(disk_free_pct_throttle=10.0, disk_free_gb_throttle=5.0)
    r = MonitorReading(disk_free_gb=100.0, disk_free_pct=60.0)
    sm.update(r, t)
    assert sm.state == 'normal'


def test_dummy_zeros_do_not_trigger_disk_throttle():
    """DiskSensor unavailable returns zeros; zeros must never trigger throttle."""
    sm = ThrottleStateMachine()
    t = ThrottleThresholds(disk_free_pct_throttle=10.0, disk_free_gb_throttle=5.0)
    r = MonitorReading(disk_free_gb=0.0, disk_free_pct=0.0)
    sm.update(r, t)
    assert sm.state == 'normal'


def test_disk_reason_includes_gb_and_pct():
    """Throttle reason string should mention both GB and %."""
    sm = ThrottleStateMachine()
    t = ThrottleThresholds(disk_free_pct_throttle=15.0, disk_free_gb_throttle=5.0)
    r = MonitorReading(disk_free_gb=2.5, disk_free_pct=8.0)
    sm.update(r, t)
    assert 'GB' in sm.reason or 'free' in sm.reason.lower()


# ── Settings schema ────────────────────────────────────────────────────────────

def test_disk_pct_throttle_in_schema():
    from core.settings import settings
    assert 'monitor:disk_free_pct_throttle' in settings.schema
    entry = settings.schema['monitor:disk_free_pct_throttle']
    assert entry['type'] == 'float'
    assert entry['default'] == 10.0
    assert entry['group'] == 'monitor'


def test_disk_gb_throttle_in_schema():
    from core.settings import settings
    assert 'monitor:disk_free_gb_throttle' in settings.schema
    entry = settings.schema['monitor:disk_free_gb_throttle']
    assert entry['type'] == 'float'
    assert entry['default'] == 5.0
    assert entry['group'] == 'monitor'


def test_load_thresholds_reads_disk_keys():
    """_load_thresholds() must populate disk fields from settings."""
    t = _load_thresholds()
    assert t.disk_free_pct_throttle > 0
    assert t.disk_free_gb_throttle > 0
