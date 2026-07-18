# Claude API Key & Model in Settings — Design Spec
**Date:** 2026-07-18
**Status:** Approved

---

## 1. Problem

`llm/factory.py`'s `claude` branch does not go through `settings.get()` at all. It opens a fresh `configparser`, reads `config.ini` directly, and calls `cfg.get('llm', 'api_key')` — which raises `NoOptionError` today, since no such key exists anywhere in `config.ini`. This violates the project's own convention (read all configurable values from `settings.get()` at call time) and means:

- The key can only ever be set by hand-editing `config.ini` — there is no schema entry, so it's invisible to the Settings UI and `settings.set()` would reject it with `KeyError` even if a UI field existed.
- Selecting `llm:provider = claude` today crashes on the first chat call instead of producing a usable error.
- The Claude model name is separately hardcoded as a constructor default (`claude-sonnet-4-6` in `llm/claude_provider.py`) and is stale — it is not a valid current Anthropic model id.

## 2. Goals

- `llm:api_key` and `llm:claude_model` become normal schema-driven settings, resolved through the existing 4-tier chain (vault_settings → settings.db → config.ini → schema default) like every other provider setting.
- Both appear in the Settings UI automatically (no frontend changes needed — the page renders directly from the schema).
- `llm/factory.py`'s `claude` branch reads both values via `settings.get()`, matching the `ollama` branch's existing pattern.
- Fix the stale default model id while touching this code.

## 3. Non-Goals

- Masking, encrypting, or otherwise changing how secrets are displayed in the Settings UI. The three existing API keys (Google Vision, Bing, HuggingFace) already render as plain-text inputs and round-trip through `GET /settings` in plaintext; the Claude key follows the same convention. Any change to that pattern is a separate, later item covering all keys at once.
- Any change to how the Google/Bing/HuggingFace keys are read or stored.

## 4. Schema (`core/settings.py`)

Two new entries added next to the existing `llm:provider` entry, same `general` group:

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

No new settings group, no frontend changes — both fields appear under the existing "general" badge on the Settings page for free.

## 5. `llm/factory.py`

Remove the `configparser`/`os` bypass block. The `claude` branch becomes:

```python
if provider == 'claude':
    from llm.claude_provider import ClaudeProvider
    return ClaudeProvider(
        api_key=settings.get('llm:api_key'),
        model=settings.get('llm:claude_model'),
    )
```

`llm/claude_provider.py` is unchanged — `ClaudeProvider.__init__` already accepts `api_key` and `model` as arguments; only its default parameter value for `model` needs updating from `claude-sonnet-4-6` to `claude-sonnet-5` (cosmetic, since `factory.py` now always passes an explicit model, but keeps the class usable standalone with a valid default).

## 6. Tests

New `TestGetProvider` class in `tests/test_llm_providers.py`:

- `provider='ollama'` still builds an `OllamaProvider` with the configured host/model (regression guard for the untouched branch).
- `provider='claude'` builds a `ClaudeProvider` using mocked `settings.get()` return values, asserting `api_key` and `model` are passed through — this is the regression guard against reintroducing the `config.ini` bypass.

## 7. Rollout

No migration needed — nothing currently has a working `llm.api_key` value in `config.ini` to preserve (the section exists but the option doesn't; today's code path errors before ever reading it successfully). Users who want the Claude provider set the key via the Settings UI after this ships. `get_provider()` is called fresh inside each query request handler (`api/routes/query.py`), not cached at startup, so both new settings take effect on the next query — no restart needed.
