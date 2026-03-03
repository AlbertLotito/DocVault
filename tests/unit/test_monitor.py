import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from core.monitor import ThrottleStateMachine, MonitorReading, ThrottleThresholds

def _thresholds():
    return ThrottleThresholds()  # all defaults

def test_normal_to_throttled_on_gpu_temp():
    sm = ThrottleStateMachine()
    r = MonitorReading(gpu_temp=82.0)
    sm.update(r, _thresholds())
    assert sm.state == 'throttled'

def test_normal_to_cooldown_on_critical_gpu_temp():
    sm = ThrottleStateMachine()
    r = MonitorReading(gpu_temp=90.0)
    sm.update(r, _thresholds())
    assert sm.state == 'cooldown'

def test_normal_no_change_on_safe_readings():
    sm = ThrottleStateMachine()
    r = MonitorReading(gpu_temp=60.0, gpu_util_pct=30.0, cpu_temp=50.0, ram_pct=40.0)
    sm.update(r, _thresholds())
    assert sm.state == 'normal'

def test_dummy_sensor_returns_zeros():
    from core.monitor import DummySensor
    s = DummySensor('test')
    r = s.read()
    assert r.gpu_temp == 0.0
    assert r.cpu_temp == 0.0

def test_dummy_sensor_never_raises():
    from core.monitor import DummySensor
    s = DummySensor('any')
    r = s.read()   # must not raise
    assert r is not None
