import time
from core import manager


def interruptible_sleep(db_path, duration, step=1):
    """Sleep duration seconds, waking early if paused.
    db_path is unused (pause state lives in settings.db) but kept for
    call-site compatibility.
    """
    slept = 0
    while slept < duration:
        if manager.get_pause_state():
            break
        time.sleep(step)
        slept += step


def should_pause_or_throttle() -> tuple[bool, str]:
    """
    Returns (should_skip, reason).
    Workers check this before claiming each task.
    """
    if manager.get_pause_state():
        return True, 'paused'
    try:
        from core.monitor import get_throttle_state
        state = get_throttle_state()
        if state == 'cooldown':
            return True, 'cooldown'
        return False, state
    except Exception:
        return False, 'normal'


def get_throttle_sleep(state: str) -> int:
    """Extra sleep between tasks based on throttle state."""
    return {'normal': 0, 'throttled': 5, 'cooldown': 30}.get(state, 0)
