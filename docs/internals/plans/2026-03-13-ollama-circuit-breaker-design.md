# Ollama Circuit Breaker — Design Document
_2026-03-13_

## Goal

When Ollama crashes or is unavailable, pause all workers, requeue in-flight tasks, and resume automatically when Ollama recovers — with a notification on both down and recovery events.

---

## Trigger Condition

Detection is **reactive, not proactive**. No background polling when Ollama is healthy.

A connection error (`ConnectionRefusedError`, `httpx.ConnectError`, or any error containing "connection" in the message) from `vision.py` or `embedder.py` triggers a single live probe. If the probe confirms Ollama is down, the circuit opens.

---

## New State: `ollama_down`

`ThrottleStateMachine` gains a new state alongside `idle`, `active`, `throttled`, `cooldown`.

- `should_pause_or_throttle()` returns `(True, "ollama_down")` in this state — all workers pause
- Transition in: any state → `ollama_down` via `report_ollama_error()`
- Transition out: `ollama_down` → previous state via probe loop detecting recovery

---

## Components

### `core/monitor.py`

**`OllamaUnavailableError`** — new exception class. Raised by `vision.py` and `embedder.py` when Ollama is confirmed down, so workers can distinguish "requeue me" from other errors.

**`report_ollama_error()`** — called by vision/embedder on connection error:
1. Probe Ollama once: `GET {ollama_host}:{ollama_port}/api/tags` with 5s timeout
2. If probe succeeds → transient error, return False (don't open circuit)
3. If probe fails → transition ThrottleStateMachine to `ollama_down`, fire `send_alert("Ollama Unavailable", ...)`, return True

**Probe recovery loop** — runs inside the existing `HardwareMonitor` thread when state is `ollama_down`:
- Probes every 30s
- On success → transition back to `idle`, fire `send_alert("Ollama Recovered", ...)`

### `extractors/vision.py` and `embeddings/embedder.py`

On connection error:
```python
from core.monitor import report_ollama_error, OllamaUnavailableError
if report_ollama_error():
    raise OllamaUnavailableError("Ollama is not reachable")
```

All other errors (CUDA, model not found, no slots) continue with existing behaviour.

### `workers/extraction_worker.py`

```python
except OllamaUnavailableError:
    manager.update_task_status(db_path, task['file_hash'], 'PENDING')
    continue  # loop → should_pause_or_throttle() blocks here
```

### `workers/embedding_worker.py`

```python
except OllamaUnavailableError:
    manager.update_task_status(db_path, task['file_hash'], 'EXTRACTED')
    continue  # loop → should_pause_or_throttle() blocks here
```

---

## Probe Implementation

```python
import requests
url = f"http://{settings.get('ollama:host')}:{settings.get('ollama:port')}/api/tags"
requests.get(url, timeout=5)
```

Uses the existing `ollama:host` / `ollama:port` settings keys.

---

## Alert Messages

| Event | Title | Level |
|---|---|---|
| Ollama goes down | "Ollama Unavailable" | `critical` |
| Ollama recovers | "Ollama Recovered" | `info` |

---

## What Is NOT in Scope

- Manual "restart Ollama" button in the UI
- Differentiating between "crashed" vs "not started"
- Per-vault Ollama config
- Partial processing (text-only tasks allowed while Ollama is down)
