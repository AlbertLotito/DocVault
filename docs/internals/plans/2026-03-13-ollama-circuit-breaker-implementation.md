# Ollama Circuit Breaker Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When Ollama crashes or is unreachable, pause all workers, requeue in-flight tasks, and automatically resume when Ollama recovers — with alerts on both events.

**Architecture:** Reactive detection — `vision.py`/`embedder.py` detect a connection error, probe Ollama once to confirm it's down, then set a new `ollama_down` flag in `core/monitor.py`. Workers already call `should_pause_or_throttle()` before claiming tasks; that check is updated to block on `ollama_down`. `HardwareMonitor` probes every 30s and clears the flag on recovery. Workers catch a new `OllamaUnavailableError` exception and requeue their current task before looping back into the paused state.

**Tech Stack:** Python stdlib `threading`, `requests` (already in project), existing `core/monitor.py` throttle state machinery.

**Files touched:**
- Modify: `core/monitor.py` — new exception, flag, probe, recovery, HardwareMonitor loop, fix governor bug
- Modify: `workers/utils.py` — add `ollama_down` to pause check
- Modify: `extractors/vision.py` — detect connection error, raise `OllamaUnavailableError`
- Modify: `embeddings/embedder.py` — same
- Modify: `workers/extraction_worker.py` — catch + requeue to PENDING
- Modify: `workers/embedding_worker.py` — catch + requeue to EXTRACTED
- Create: `tests/test_ollama_circuit_breaker.py`

---

### Task 1: core/monitor.py — circuit breaker core

**Files:**
- Modify: `core/monitor.py`
- Test: `tests/test_ollama_circuit_breaker.py`

**Step 1: Write the failing tests**

Create `tests/test_ollama_circuit_breaker.py`:

```python
"""
tests/test_ollama_circuit_breaker.py
Unit tests for the Ollama circuit breaker in core/monitor.py.
Run: pytest tests/test_ollama_circuit_breaker.py -v
"""
import threading
from unittest.mock import patch, MagicMock
import pytest

import core.monitor as monitor


@pytest.fixture(autouse=True)
def reset_ollama_state():
    """Reset the global ollama_down flag before each test."""
    with monitor._throttle_lock:
        monitor._ollama_down = False
    yield
    with monitor._throttle_lock:
        monitor._ollama_down = False


# --- OllamaUnavailableError ---

def test_ollama_unavailable_error_is_exception():
    from core.monitor import OllamaUnavailableError
    e = OllamaUnavailableError("test")
    assert isinstance(e, Exception)
    assert str(e) == "test"


# --- _probe_ollama ---

def test_probe_ollama_returns_true_on_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch('requests.get', return_value=mock_resp):
        assert monitor._probe_ollama() is True


def test_probe_ollama_returns_false_on_connection_error():
    import requests
    with patch('requests.get', side_effect=requests.ConnectionError("refused")):
        assert monitor._probe_ollama() is False


def test_probe_ollama_returns_false_on_timeout():
    import requests
    with patch('requests.get', side_effect=requests.Timeout()):
        assert monitor._probe_ollama() is False


# --- report_ollama_error ---

def test_report_ollama_error_returns_false_when_transient():
    """Probe succeeds → transient error, flag stays False."""
    mock_resp = MagicMock()
    with patch('requests.get', return_value=mock_resp):
        result = monitor.report_ollama_error()
    assert result is False
    with monitor._throttle_lock:
        assert monitor._ollama_down is False


def test_report_ollama_error_returns_true_when_confirmed_down():
    """Probe fails → confirmed down, flag set to True."""
    import requests
    with patch('requests.get', side_effect=requests.ConnectionError()):
        with patch('core.monitor.send_alert') as mock_alert:
            result = monitor.report_ollama_error()
    assert result is True
    with monitor._throttle_lock:
        assert monitor._ollama_down is True
    mock_alert.assert_called_once()


def test_report_ollama_error_only_alerts_once():
    """Second call when already down does not fire a second alert."""
    import requests
    with monitor._throttle_lock:
        monitor._ollama_down = True  # already down
    with patch('requests.get', side_effect=requests.ConnectionError()):
        with patch('core.monitor.send_alert') as mock_alert:
            monitor.report_ollama_error()
    mock_alert.assert_not_called()


# --- _mark_ollama_recovered ---

def test_mark_ollama_recovered_clears_flag():
    with monitor._throttle_lock:
        monitor._ollama_down = True
    with patch('core.monitor.send_alert'):
        monitor._mark_ollama_recovered()
    with monitor._throttle_lock:
        assert monitor._ollama_down is False


def test_mark_ollama_recovered_sends_alert():
    with monitor._throttle_lock:
        monitor._ollama_down = True
    with patch('core.monitor.send_alert') as mock_alert:
        monitor._mark_ollama_recovered()
    mock_alert.assert_called_once()
    args = mock_alert.call_args[0]
    assert 'recover' in args[0].lower() or 'recover' in args[1].lower()


# --- get_throttle_state with ollama_down ---

def test_get_throttle_state_returns_ollama_down_when_flag_set():
    with monitor._throttle_lock:
        monitor._ollama_down = True
    state, reason = monitor.get_throttle_state()
    assert state == 'ollama_down'
    assert reason != ''


def test_get_throttle_state_normal_when_flag_clear():
    with monitor._throttle_lock:
        monitor._ollama_down = False
        monitor._throttle_state = 'normal'
        monitor._throttle_reason = ''
    state, reason = monitor.get_throttle_state()
    assert state == 'normal'
```

**Step 2: Run tests — verify they fail**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/test_ollama_circuit_breaker.py -v 2>&1 | head -30
```

Expected: `AttributeError: module 'core.monitor' has no attribute '_ollama_down'` or similar.

**Step 3: Implement the changes in core/monitor.py**

Make the following additions/changes to `E:\DocVault\core\monitor.py`:

**3a. Add import at top (after existing imports):**
```python
import requests as _requests
```

**3b. Add after the `_last_reading` global (around line 308):**
```python
# Ollama circuit breaker state
_ollama_down: bool = False
```

**3c. Add new exception class after the dataclasses section (after ThrottleThresholds, before BaseSensor):**
```python
class OllamaUnavailableError(Exception):
    """Raised by vision/embedder when Ollama is confirmed unreachable."""
```

**3d. Add these three functions before `HardwareMonitor` class:**
```python
def _probe_ollama() -> bool:
    """Returns True if Ollama responds to a lightweight ping."""
    try:
        from core.settings import settings
        host = settings.get('ollama:host') or 'localhost'
        port = settings.get('ollama:port') or '11434'
        _requests.get(f"http://{host}:{port}/api/tags", timeout=5)
        return True
    except Exception:
        return False


def report_ollama_error() -> bool:
    """
    Called by vision/embedder on a connection error.
    Probes Ollama once to distinguish transient from sustained outage.
    Returns True if confirmed down (caller should raise OllamaUnavailableError).
    Returns False if probe succeeded (transient error, caller should continue).
    """
    global _ollama_down
    if _probe_ollama():
        return False  # Transient — Ollama is still up

    with _throttle_lock:
        already_down = _ollama_down
        _ollama_down = True

    if not already_down:
        logger.error("Ollama is not reachable — entering ollama_down state. Workers paused.", ext="monitor")
        try:
            send_alert(
                "Ollama Unavailable",
                "Connection failed. All workers paused and tasks requeued. Retrying every 30s.",
                level="critical",
                source="monitor",
            )
        except Exception:
            pass
    return True


def _mark_ollama_recovered():
    """Called by HardwareMonitor probe loop when Ollama comes back online."""
    global _ollama_down
    with _throttle_lock:
        _ollama_down = False
    logger.info("Ollama recovered — workers resuming", ext="monitor")
    try:
        send_alert(
            "Ollama Recovered",
            "Ollama is back online. Workers resuming normally.",
            level="info",
            source="monitor",
        )
    except Exception:
        pass
```

**3e. Add `send_alert` import — add near the top of `report_ollama_error` usage. Since `send_alert` is already imported in `core/alerts.py`, add this import at module level after `from core import logger`:**
```python
from core.alerts import send_alert
```

**3f. Update `get_throttle_state()` — add `ollama_down` check at the TOP of the function, before all other checks:**
```python
def get_throttle_state() -> str:
    """Returns (state, reason) tuple."""
    with _throttle_lock:
        # Ollama down takes priority over all other states
        if _ollama_down:
            return 'ollama_down', 'Ollama is not reachable — workers paused'

        state = _throttle_state
        reason = _throttle_reason

        # Hardware protection (cooldown) always wins
        if state == 'cooldown':
            return 'cooldown', reason

        # Check for search-triggered throttle
        if _last_user_activity > 0:
            try:
                from core.settings import settings
                duration = int(settings.get('monitor:search_throttle_duration') or 60)
                elapsed = time.monotonic() - _last_user_activity
                if elapsed < duration:
                    return 'throttled', f"User activity detected ({int(duration - elapsed)}s remaining)"
            except Exception:
                pass

        return state, reason
```

**3g. Fix the existing bug in `ollama_governor()` — change the two `if state in (...)` checks to unpack the tuple:**

Find these two occurrences in `ollama_governor()`:
```python
state = get_throttle_state()
if state in ('throttled', 'cooldown'):
```
Replace each with:
```python
state, _reason = get_throttle_state()
if state in ('throttled', 'cooldown'):
```

**3h. Update `HardwareMonitor.run()` — add ollama recovery probe:**
```python
def run(self):
    """Main daemon loop. Call from a daemon thread."""
    logger.info("Resource governor starting", ext="monitor")
    _last_ollama_probe: float = 0.0
    _ollama_probe_interval: int = 30  # seconds

    while True:
        if self._is_enabled():
            reading    = self._sample()
            thresholds = _load_thresholds()
            self._sm.update(reading, thresholds)
            _set_throttle_state(self._sm.state, reading, self._sm.reason)
            _record_sample(reading, self._sm.state)

        # Ollama recovery probe — only fires when circuit is open
        now = time.monotonic()
        if _ollama_down and (now - _last_ollama_probe) >= _ollama_probe_interval:
            _last_ollama_probe = now
            if _probe_ollama():
                _mark_ollama_recovered()

        # Sleep shorter when waiting for Ollama recovery so probe fires on time
        time.sleep(5 if _ollama_down else self._get_interval())
```

**Step 4: Run tests — all must pass**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/test_ollama_circuit_breaker.py -v
```

Expected: All tests PASS.

**Step 5: Commit**

```bash
git add core/monitor.py tests/test_ollama_circuit_breaker.py
git commit -m "feat: add Ollama circuit breaker to resource governor (OllamaUnavailableError, probe, recovery)"
```

---

### Task 2: workers/utils.py — block workers on ollama_down

**Files:**
- Modify: `workers/utils.py`

**Step 1: Update `should_pause_or_throttle()`**

Current (line 28):
```python
if state == 'cooldown':
    return True, 'cooldown'
```

Replace with:
```python
if state in ('cooldown', 'ollama_down'):
    return True, reason
```

Full updated function:
```python
def should_pause_or_throttle() -> tuple[bool, str]:
    """
    Returns (should_skip, reason).
    Workers check this before claiming each task.
    """
    if manager.get_pause_state():
        return True, 'paused'
    try:
        from core.monitor import get_throttle_state
        state, reason = get_throttle_state()
        if state in ('cooldown', 'ollama_down'):
            return True, reason
        return False, state
    except Exception:
        return False, 'normal'
```

**Step 2: Run existing tests to confirm no regression**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: All previously passing tests still pass.

**Step 3: Commit**

```bash
git add workers/utils.py
git commit -m "feat: block workers on ollama_down state in should_pause_or_throttle"
```

---

### Task 3: vision.py and embedder.py — detect and raise

**Files:**
- Modify: `extractors/vision.py`
- Modify: `embeddings/embedder.py`

**Step 1: Update `extractors/vision.py`**

In the `describe()` function, find the connection error branch inside the `except Exception as e:` block:

```python
if 'connection' in err.lower():
    print(f"  [vision] Connection error: Is Ollama running? {err}")
```

Replace with:
```python
if 'connection' in err.lower() or isinstance(e, (ConnectionRefusedError, ConnectionError)):
    from core.monitor import report_ollama_error, OllamaUnavailableError
    if report_ollama_error():
        raise OllamaUnavailableError("Ollama is not reachable") from e
    logger.warn(f"[vision] Transient connection error: {err}", ext="vision")
```

**Step 2: Update `embeddings/embedder.py`**

In the `embed()` function, find the generic `except Exception as e:` block:

```python
except Exception as e:
    err = str(e)
    if 'no slots' in err.lower() and attempt < _NO_SLOTS_RETRIES:
        wait = _NO_SLOTS_BACKOFF[attempt]
        print(f"  [embed] Ollama busy (no slots), retry {attempt + 1}/{_NO_SLOTS_RETRIES} in {wait}s…")
        time.sleep(wait)
        continue
    print(f"  [embed] Error: {e}")
    return None
```

Replace with:
```python
except Exception as e:
    err = str(e)
    if 'no slots' in err.lower() and attempt < _NO_SLOTS_RETRIES:
        wait = _NO_SLOTS_BACKOFF[attempt]
        print(f"  [embed] Ollama busy (no slots), retry {attempt + 1}/{_NO_SLOTS_RETRIES} in {wait}s…")
        time.sleep(wait)
        continue
    if 'connection' in err.lower() or isinstance(e, (ConnectionRefusedError, ConnectionError)):
        from core.monitor import report_ollama_error, OllamaUnavailableError
        if report_ollama_error():
            raise OllamaUnavailableError("Ollama is not reachable") from e
    print(f"  [embed] Error: {e}")
    return None
```

**Step 3: Run tests**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/test_ollama_circuit_breaker.py tests/test_embeddings.py -v --tb=short
```

Expected: All pass.

**Step 4: Commit**

```bash
git add extractors/vision.py embeddings/embedder.py
git commit -m "feat: raise OllamaUnavailableError on confirmed connection failure in vision and embedder"
```

---

### Task 4: Workers — catch and requeue

**Files:**
- Modify: `workers/extraction_worker.py`
- Modify: `workers/embedding_worker.py`

**Step 1: Update `workers/extraction_worker.py`**

Add import at the top:
```python
from core.monitor import OllamaUnavailableError
```

In `run()`, find the task processing block:
```python
if task:
    try:
        process_task(db_path, task)
    except Exception as e:
        logger.error(f"Extraction worker unhandled: {e}")
        manager.complete_extraction(db_path, task['file_hash'],
                                    status='ERROR', error=str(e))
```

Replace with:
```python
if task:
    try:
        process_task(db_path, task)
    except OllamaUnavailableError:
        logger.warn(f"Ollama unavailable — requeueing {task['file_hash']} to PENDING")
        manager.update_task_status(db_path, task['file_hash'], 'PENDING')
        # Loop continues; should_pause_or_throttle() will block on 'ollama_down'
    except Exception as e:
        logger.error(f"Extraction worker unhandled: {e}")
        manager.complete_extraction(db_path, task['file_hash'],
                                    status='ERROR', error=str(e))
```

**Step 2: Update `workers/embedding_worker.py`**

Add import at the top:
```python
from core.monitor import OllamaUnavailableError
```

In `run()`, find the task processing block:
```python
if task:
    try:
        process_task(db_path, task, vs)
    except Exception as e:
        if "connection" in str(e).lower():
            logger.error(f"Qdrant connection lost. Will attempt to reconnect. Error: {e}")
            vs = None
        else:
            logger.error(f"Unhandled exception in embedding worker for task {task.get('file_hash')}: {e}")
        manager.update_task_status(db_path, task['file_hash'], status='ERROR')
```

Replace with:
```python
if task:
    try:
        process_task(db_path, task, vs)
    except OllamaUnavailableError:
        logger.warn(f"Ollama unavailable — requeueing {task['file_hash']} to EXTRACTED")
        manager.update_task_status(db_path, task['file_hash'], 'EXTRACTED')
        # Loop continues; should_pause_or_throttle() will block on 'ollama_down'
    except Exception as e:
        if "connection" in str(e).lower():
            logger.error(f"Qdrant connection lost. Will attempt to reconnect. Error: {e}")
            vs = None
        else:
            logger.error(f"Unhandled exception in embedding worker for task {task.get('file_hash')}: {e}")
        manager.update_task_status(db_path, task['file_hash'], status='ERROR')
```

**Step 3: Run full test suite**

```bash
cd E:/DocVault && venv/Scripts/python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: All tests pass, no regressions.

**Step 4: Commit**

```bash
git add workers/extraction_worker.py workers/embedding_worker.py
git commit -m "feat: catch OllamaUnavailableError in workers, requeue tasks on Ollama outage"
```

---

### Notes

- **`manager.update_task_status()`**: This function must accept `'PENDING'` and `'EXTRACTED'` as valid statuses. Verify it exists in `core/manager.py` — if only `complete_extraction()` exists, use `complete_extraction(db_path, file_hash, status='PENDING')` for the extraction worker instead.
- **`requests` import**: `requests` is already a project dependency (used by `workers/art_enrichment_worker.py`). No new pip install needed.
- **`send_alert` circular import risk**: `core/monitor.py` importing from `core/alerts.py` — check `core/alerts.py` does not import from `core/monitor.py`. If it does, move the `send_alert` import inside the functions rather than at module level.
- **Art enrichment worker**: Uses Ollama directly via `requests`. Out of scope for this PR — separate follow-up.
- **LLM/RAG queries**: User-facing, not background workers. Out of scope.
- **Smoke test after deploy**: Kill Ollama (`taskkill /IM ollama.exe /F`), let a vision task run, observe: task requeued to PENDING, alert fires, workers pause. Restart Ollama, observe: workers resume within 30s, recovery alert fires.
