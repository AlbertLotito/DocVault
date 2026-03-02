import time
from core import manager

def interruptible_sleep(db_path, duration, step=1):
    """
    Sleeps for a 'duration' in seconds, but checks the pause state
    every 'step' seconds and exits early if paused.
    db_path is unused (pause state lives in settings.db) but kept for
    call-site compatibility.
    """
    slept = 0
    while slept < duration:
        if manager.get_pause_state():
            break
        time.sleep(step)
        slept += step
