# Claude API Key & Model Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Claude LLM provider's API key and model name normal `settings.get()`-driven configuration, visible and editable in the Settings UI, instead of a hardcoded `config.ini` bypass in `llm/factory.py` that currently crashes on use — and make Claude actually answer questions through the Ask UI's streaming endpoint, not just the older non-streaming one.

**Architecture:** Two new entries in the existing `core/settings.py` schema dict (`llm:api_key`, `llm:claude_model`), consumed by `llm/factory.py`'s `claude` branch via `settings.get()` — the same pattern the `ollama` branch already uses. No new files, no frontend changes (the Settings page renders schema entries automatically).

**Tech Stack:** Python 3.13, pytest, unittest.mock.

## Global Constraints

- Settings must be read via `settings.get()` at call time, never at import time (project convention, `CLAUDE.md`).
- New schema entries go in the `general` group, matching `llm:provider`'s existing group placement (spec §4).
- `llm:claude_model` default must be `claude-sonnet-5`, not the stale `claude-sonnet-4-6` (spec §1, §4).
- No changes to Google Vision, Bing, or HuggingFace key handling, and no masking/encryption added to the Claude key field — it stays a plain-text `type: 'string'` setting like the other three keys (spec §3).
- `ClaudeProvider.chat_stream` must match `OllamaProvider.chat_stream`'s existing contract exactly: a synchronous generator yielding plain text chunks, with failures caught internally and yielded as a `"\n\n[... error: ...]"` string rather than raised — `api/routes/query.py`'s `/query/stream` consumer (`for chunk in llm.chat_stream(messages):`) must work unmodified against either provider (final-review finding, added post-Task-1).

---

### Task 1: Add `llm:api_key` / `llm:claude_model` settings and wire them into the Claude provider

**Files:**
- Modify: `core/settings.py:21-24` (insert two new schema entries after the existing `llm:provider` entry)
- Modify: `llm/factory.py` (replace the `configparser` bypass in the `claude` branch)
- Modify: `llm/claude_provider.py:6` (fix stale default model id)
- Test: `tests/test_llm_providers.py` (new `TestGetProvider` class)

**Interfaces:**
- Consumes: `core.settings.settings.get(key: str) -> str | None` (existing, unchanged signature)
- Produces: `llm.factory.get_provider() -> OllamaProvider | ClaudeProvider` (existing function, behavior changes for the `claude` branch only). `ClaudeProvider(api_key: str, model: str = 'claude-sonnet-5')` (existing class, only the default value of `model` changes).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_providers.py`:

```python
from llm.factory import get_provider
from llm.claude_provider import ClaudeProvider


class TestGetProvider:
    @patch('llm.factory.settings')
    def test_ollama_provider_uses_settings(self, mock_settings):
        values = {
            'llm:provider': 'ollama',
            'ollama:chat_model': 'qwen2.5:14b',
            'ollama:host': 'http://localhost:11600',
        }
        mock_settings.get.side_effect = lambda key: values[key]
        provider = get_provider()
        assert isinstance(provider, OllamaProvider)
        assert provider.model == 'qwen2.5:14b'
        assert provider.host == 'http://localhost:11600'

    @patch('llm.factory.settings')
    def test_claude_provider_uses_settings(self, mock_settings):
        values = {
            'llm:provider': 'claude',
            'llm:api_key': 'sk-ant-test123',
            'llm:claude_model': 'claude-sonnet-5',
        }
        mock_settings.get.side_effect = lambda key: values[key]
        provider = get_provider()
        assert isinstance(provider, ClaudeProvider)
        assert provider.api_key == 'sk-ant-test123'
        assert provider.model == 'claude-sonnet-5'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_providers.py::TestGetProvider -v`

Expected: `test_claude_provider_uses_settings` FAILS. The current `llm/factory.py` claude branch ignores the mocked `settings.get` entirely and opens a real `configparser` against `config.ini`, which raises `configparser.NoOptionError: No option 'api_key' in section: 'llm'` (there is no `[llm] api_key` entry in `config.ini` today). `test_ollama_provider_uses_settings` should PASS already — it's a regression guard for the untouched branch, included to confirm it stays green after Task 1's edits.

- [ ] **Step 3: Add the two new settings to the schema**

In `core/settings.py`, insert immediately after the existing `llm:provider` entry (currently lines 21-24) and before the `tesseract:path` entry:

```python
            'llm:api_key': {
                'type': 'string', 'default': '', 'label': 'Claude API Key', 'group': 'general',
                'description': 'Anthropic API key. Required only when llm:provider is set to "claude". '
                               'Get a key from console.anthropic.com. Takes effect on the next query — no restart needed.',
            },
            'llm:claude_model': {
                'type': 'string', 'default': 'claude-sonnet-5', 'label': 'Claude Model', 'group': 'general',
                'description': 'Anthropic model used for RAG answers when llm:provider is "claude". '
                               'Default: claude-sonnet-5.',
            },
```

- [ ] **Step 4: Replace the `claude` branch in `llm/factory.py`**

Replace the full contents of `llm/factory.py` with:

```python
from core.settings import settings


def get_provider():
    provider = settings.get('llm:provider')

    if provider == 'ollama':
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(
            model=settings.get('ollama:chat_model'),
            host=settings.get('ollama:host'),
        )
    if provider == 'claude':
        from llm.claude_provider import ClaudeProvider
        return ClaudeProvider(
            api_key=settings.get('llm:api_key'),
            model=settings.get('llm:claude_model'),
        )
    raise ValueError(f"Unknown LLM provider: {provider}")
```

This removes the `import configparser, os` bypass block entirely.

- [ ] **Step 5: Fix the stale default model id in `llm/claude_provider.py`**

In `llm/claude_provider.py`, change line 6:

```python
    def __init__(self, api_key: str, model: str = 'claude-sonnet-4-6'):
```

to:

```python
    def __init__(self, api_key: str, model: str = 'claude-sonnet-5'):
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_llm_providers.py -v`

Expected: all tests PASS, including both new `TestGetProvider` tests and the pre-existing `TestOllamaProvider` tests (unaffected by this change).

- [ ] **Step 7: Commit**

```bash
git add core/settings.py llm/factory.py llm/claude_provider.py tests/test_llm_providers.py
git commit -m "$(cat <<'EOF'
feat: expose Claude API key and model as settings, fix stale default

llm/factory.py previously bypassed settings.get() for the claude
provider branch, reading config.ini directly via a fresh configparser
with no schema entry -- making the key impossible to configure via
the Settings UI and crashing on first use (NoOptionError, since no
[llm] api_key was ever set anywhere). Added llm:api_key and
llm:claude_model to the settings schema, consumed via settings.get()
like every other provider setting. Also fixes claude_provider.py's
hardcoded default model id (claude-sonnet-4-6, not a valid model)
to claude-sonnet-5.
EOF
)"
```

---

### Task 2: Add `ClaudeProvider.chat_stream` so Claude works through the streaming Ask endpoint

**Context:** The final whole-branch review of Task 1 found that `api/routes/query.py`'s `/query/stream` endpoint (what the Ask panel in `frontend/search.html` actually calls) invokes `llm.chat_stream(messages)`, but `ClaudeProvider` only implements `chat()`. Selecting `llm:provider=claude` and asking a question in the UI raises `AttributeError: 'ClaudeProvider' object has no attribute 'chat_stream'`, caught by `query.py` and surfaced as an SSE error event. The non-streaming `/query` endpoint already works with Claude (uses `rag_query()`→`chat()`); this task closes the gap for the streaming path.

**Files:**
- Modify: `llm/claude_provider.py` (add `chat_stream` method, add `import json` at the top)
- Test: `tests/test_llm_providers.py` (new `TestClaudeProvider` class)

**Interfaces:**
- Consumes: `httpx.stream(method: str, url: str, *, headers: dict, json: dict, timeout: int) -> context manager yielding a Response with .raise_for_status() and .iter_lines() -> Iterator[str]` (httpx's existing streaming API, same library already imported in this file for `chat()`)
- Produces: `ClaudeProvider.chat_stream(self, messages: list[dict]) -> Generator[str, None, None]` — consumed unmodified by `api/routes/query.py:182`'s `for chunk in llm.chat_stream(messages):`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_providers.py`:

```python
import json


class TestClaudeProvider:
    @patch('llm.claude_provider.httpx.stream')
    def test_chat_stream_yields_text_deltas(self, mock_stream):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_lines.return_value = [
            'data: {"type": "message_start"}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}',
            'data: {"type": "message_stop"}',
        ]
        mock_stream.return_value.__enter__.return_value = mock_response
        provider = ClaudeProvider(api_key='test-key')
        chunks = list(provider.chat_stream([{'role': 'user', 'content': 'hi'}]))
        assert chunks == ['Hello', ' world']

    @patch('llm.claude_provider.httpx.stream')
    def test_chat_stream_yields_error_string_on_failure(self, mock_stream):
        mock_stream.side_effect = Exception("Connection refused")
        provider = ClaudeProvider(api_key='test-key')
        chunks = list(provider.chat_stream([{'role': 'user', 'content': 'hi'}]))
        assert len(chunks) == 1
        assert 'Claude error' in chunks[0]
        assert 'Connection refused' in chunks[0]
```

`ClaudeProvider` is already imported by Task 1's `TestGetProvider` class — no new import needed for that name, but add `import json` at the top of the test file alongside the existing `from unittest.mock import patch, MagicMock` line if it is not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_providers.py::TestClaudeProvider -v`

Expected: both tests FAIL with `AttributeError: 'ClaudeProvider' object has no attribute 'chat_stream'`.

- [ ] **Step 3: Implement `chat_stream` in `llm/claude_provider.py`**

Add `import json` to the top of the file (alongside the existing `import httpx`), then add this method to the `ClaudeProvider` class, after the existing `chat` method:

```python
    def chat_stream(self, messages: list[dict]):
        """Yield response text chunks as they are generated by the LLM."""
        try:
            with httpx.stream(
                'POST',
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': self.api_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': self.model,
                    'max_tokens': 1024,
                    'messages': messages,
                    'stream': True,
                },
                timeout=30,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith('data: '):
                        continue
                    event = json.loads(line[len('data: '):])
                    if event.get('type') == 'content_block_delta':
                        delta = event.get('delta', {})
                        if delta.get('type') == 'text_delta':
                            yield delta.get('text', '')
        except Exception as e:
            yield f"\n\n[Claude error: {e}]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_providers.py -v`

Expected: all tests PASS — the two new `TestClaudeProvider` tests, plus every test from Task 1 and the pre-existing `TestOllamaProvider` tests (unaffected).

- [ ] **Step 5: Commit**

```bash
git add llm/claude_provider.py tests/test_llm_providers.py
git commit -m "$(cat <<'EOF'
feat: add ClaudeProvider.chat_stream for the streaming Ask endpoint

api/routes/query.py's /query/stream endpoint (what the Ask panel
actually calls) invokes llm.chat_stream(messages), but ClaudeProvider
only implemented chat() -- selecting llm:provider=claude and asking a
question in the UI raised AttributeError. Adds chat_stream using
Anthropic's SSE streaming format, matching OllamaProvider.chat_stream's
contract exactly (sync generator, errors yielded as a string rather
than raised) so query.py's consumer needs no changes.
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- §4 schema entries (`llm:api_key`, `llm:claude_model`, group `general`, exact descriptions) → Task 1 Step 3. ✓
- §5 `factory.py` rewrite → Task 1 Step 4. ✓
- §5 default model fix in `claude_provider.py` → Task 1 Step 5. ✓
- §6 tests (`TestGetProvider`, ollama regression + claude assertion) → Task 1 Steps 1-2, 6. ✓
- §7 rollout (no migration, no restart) → no code change needed; the settings values are read fresh per-request by `get_provider()`, already true before this plan — no separate task required.
- §3 non-goals (no masking, no changes to other keys) → nothing in this plan touches those files. ✓

**Placeholder scan:** No TBD/TODO/"add appropriate" phrasing; all code blocks are complete and copy-pasteable.

**Type consistency:** `get_provider()` signature (no args, returns `OllamaProvider | ClaudeProvider`) is identical before and after. `ClaudeProvider(api_key: str, model: str = ...)` and `OllamaProvider(model: str, host: str)` constructor signatures match what Task 1's tests assert against and what `llm/ollama_provider.py`/`llm/claude_provider.py` already define — no renames introduced.

**Task 2 addendum (post-Task-1 final-review finding):**
- Coverage: the finding ("`ClaudeProvider` lacks `chat_stream`, breaking the streaming Ask endpoint") → Task 2 in full. ✓
- Placeholder scan: Task 2's code blocks are complete; no TBD/TODO.
- Type consistency: `chat_stream(self, messages: list[dict])` is a generator, matching `OllamaProvider.chat_stream`'s signature and yield-string-on-error behavior exactly, per the new Global Constraint. `query.py:182`'s consumption (`for chunk in llm.chat_stream(messages):`) requires no change for either provider.
- Scope: Task 2 touches only `llm/claude_provider.py` and its test file — no interaction with Task 1's `core/settings.py`/`llm/factory.py` changes beyond already-merged code.

Two tasks total; each independently testable and committable.
