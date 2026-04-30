import time
from core import manager


def interruptible_sleep(db_path, duration, step=1):
    """Sleep duration seconds, waking early if paused.
    db_path is unused (pause state lives in settings.db) but kept for
    call-site compatibility.
    """
    slept = 0
    while slept < duration:
        time.sleep(step)
        slept += step
        if manager.get_pause_state():
            break


def paused_sleep(duration=30, step=5):
    """Sleep while paused, waking as soon as the pause is lifted.
    Uses a long step to avoid spinning; max resume latency is step seconds.
    """
    slept = 0
    while slept < duration:
        time.sleep(step)
        slept += step
        if not manager.get_pause_state():
            break


def should_pause_or_throttle() -> tuple[bool, str]:
    """
    Returns (should_skip, reason).
    Workers check this before claiming each task.
    """
    if manager.get_pause_state():
        return True, 'paused'

    # Inline RAM check — bypass the 60s monitor sample cycle.
    try:
        import psutil
        from core.settings import settings as _s
        threshold = float(_s.get('monitor:ram_emergency_pct') or 92)
        if psutil.virtual_memory().percent >= threshold:
            return True, 'cooldown'
    except Exception:
        pass

    try:
        from core.monitor import get_throttle_state
        state, reason = get_throttle_state()
        if state == 'cooldown':
            return True, 'cooldown'
        return False, state
    except Exception:
        return False, 'normal'


def get_throttle_sleep(state: str) -> int:
    """Extra sleep between tasks based on throttle state."""
    return {'normal': 0, 'throttled': 5, 'cooldown': 30}.get(state, 0)
