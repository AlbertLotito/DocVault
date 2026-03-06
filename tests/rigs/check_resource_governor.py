#!/usr/bin/env python3
"""
check_resource_governor.py -- Validates sensor init and throttle state machine.

Uses injected mock readings to exercise all state transitions without
needing real hardware.

Exit codes: 0 = pass, 1 = fail

Usage:
  python tests/rigs/check_resource_governor.py
  python tests/rigs/check_resource_governor.py --verbose
"""
import argparse, sys, os

GREEN = '\033[92m'; RED = '\033[91m'; RESET = '\033[0m'
def _pass(msg): print(f"{GREEN}PASS{RESET}  {msg}"); return True
def _fail(msg): print(f"{RED}FAIL{RESET}  {msg}"); return False

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verbose', '-v', action='store_true')
    args = parser.parse_args()

    print("\nDocVault Layer 2 -- Resource Governor Check\n")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

    from core.monitor import (ThrottleStateMachine, MonitorReading,
                               ThrottleThresholds, DummySensor)
    results = []
    t = ThrottleThresholds()

    # Dummy sensor
    d = DummySensor('test')
    d.initialise()
    r = d.read()
    results.append(_pass("DummySensor reads without raising") if r is not None
                   else _fail("DummySensor raised"))

    # Normal -> throttled on GPU temp
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=82.0), t)
    results.append(_pass("Normal -> Throttled on GPU temp 82C") if sm.state == 'throttled'
                   else _fail(f"Expected throttled, got {sm.state}"))

    # Normal -> cooldown on critical GPU temp
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=90.0), t)
    results.append(_pass("Normal -> Cooldown on GPU temp 90C") if sm.state == 'cooldown'
                   else _fail(f"Expected cooldown, got {sm.state}"))

    # Throttled -> normal when pressure clears
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=82.0), t)
    sm.update(MonitorReading(gpu_temp=60.0), t)
    results.append(_pass("Throttled -> Normal when pressure clears") if sm.state == 'normal'
                   else _fail(f"Expected normal, got {sm.state}"))

    # Normal stays Normal on safe readings
    sm = ThrottleStateMachine()
    sm.update(MonitorReading(gpu_temp=50.0, gpu_util_pct=20.0, cpu_temp=40.0, ram_pct=30.0), t)
    results.append(_pass("Normal stays Normal on safe readings") if sm.state == 'normal'
                   else _fail(f"Expected normal, got {sm.state}"))

    # Search Triggered Throttle
    from core.monitor import notify_user_activity, get_throttle_state, _set_throttle_state
    _set_throttle_state('normal', MonitorReading())
    notify_user_activity()
    state = get_throttle_state()
    results.append(_pass("Search triggers Throttled state") if state == 'throttled'
                   else _fail(f"Expected throttled after search, got {state}"))

    # Cooldown Priority over Search Throttle
    _set_throttle_state('cooldown', MonitorReading())
    state = get_throttle_state()
    results.append(_pass("Cooldown takes priority over Search throttle") if state == 'cooldown'
                   else _fail(f"Expected cooldown, got {state}"))

    # RAM pressure

    sm = ThrottleStateMachine()
    sm.update(MonitorReading(ram_pct=90.0), t)
    results.append(_pass("Normal -> Throttled on RAM 90%") if sm.state == 'throttled'
                   else _fail(f"Expected throttled, got {sm.state}"))

    failed = sum(1 for r in results if not r)
    print(f"\n{len(results)-failed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

if __name__ == '__main__':
    main()
