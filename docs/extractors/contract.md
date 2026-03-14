# DocVault Kernel Contract Reference

This document describes the interface every DocVault extractor kernel must implement.

---

## 1. Overview

A DocVault extractor (called a "kernel") is a Python file with two required components:

1. A `MANIFEST` dict at module level
2. A typed `extract()` function

Any Python file in the `extractors/` directory matching this contract is automatically discovered, registered, and routed to matching file extensions. No registration step is needed beyond dropping the file in place.

---

## 2. MANIFEST Specification

```python
MANIFEST = {
    "id":         "com.docvault.text.plain",   # reverse-domain ID, globally unique
    "version":    "1.0.0",                      # semantic version string
    "name":       "Plain Text Engine",           # human-readable display name
    "extensions": [".txt", ".md", ".rst"],       # lowercase, dot-prefixed
    "requires":   [],                            # pip package names (optional)
}
```

### Required keys

| Key | Type | Description |
|-----|------|-------------|
| `id` | `str` | Globally unique reverse-domain identifier |
| `version` | `str` | Semantic version string (e.g. `"1.0.0"`) |
| `name` | `str` | Human-readable display name shown in the Extractor Lab |
| `extensions` | `list[str]` | Lowercase, dot-prefixed file extensions this kernel handles |

### Optional keys

| Key | Type | Description |
|-----|------|-------------|
| `requires` | `list[str]` | pip package names the kernel depends on (informational only; DocVault does not auto-install) |
| `target_type` | `str` | `"file"` (default) or `"folder"` for directory-level extractors |

### ID convention

Built-in kernels use `com.docvault.<domain>.<name>`. Third-party kernels should use their own reverse-domain prefix:

```
com.yourcompany.myteam.extractor
```

### Discovery and static analysis

The registry reads `MANIFEST` using AST inspection — it does **not** execute the module to discover it. This means manifests must be **static dict literals**: no computed values, no function calls, no variable references.

```python
# CORRECT — static literal, AST-readable
MANIFEST = {
    "id": "com.example.mykernel",
    "extensions": [".foo"],
    ...
}

# WRONG — computed value, invisible to AST scan
MANIFEST = {
    "id": make_id("mykernel"),   # will not be discovered
    "extensions": get_exts(),    # will not be discovered
}
```

---

## 3. The `extract()` Function

### Required signature (exact)

```python
from core.extractors.base import ExtractorContext, IngestResult

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    ...
```

The contract auditor verifies:

- Function is named `extract`
- First parameter is `file_path` with annotation `str`
- Second parameter is `ctx` with annotation `ExtractorContext`

Any deviation in parameter names or type annotations will fail the signature check and prevent certification.

### Return values

```python
return result, None          # success: IngestResult + no error
return None, "error message" # failure: no result + error string
```

The second element of the tuple is always `None` on success or a non-empty `str` describing the failure. Never raise exceptions out of `extract()` — catch them and return `(None, str(e))`.

---

## 4. `ExtractorContext` Reference

Passed to every `extract()` call by the framework. Treat it as read-only.

| Field | Type | Description |
|-------|------|-------------|
| `vault_id` | `str \| None` | The vault being processed. `None` for legacy tasks. |
| `file_hash` | `str` | SHA-256 hash of the file. Stable identifier regardless of path. |
| `cancel_token` | `threading.Event` | Set when the worker is shutting down. Check `.is_set()` in long loops. |
| `logger` | Logger | Pre-configured logger. Use instead of `print()`. |
| `settings` | Settings | Settings resolver. Call `.get(group, key)` at use time, not at import time. |
| `timeout_secs` | `int` | Worker timeout budget in seconds. Respect it in long-running operations. |
| `report_progress` | `Callable` | Optional progress callback: `(current: int, total: int, message: str) -> None`. |

### Cooperative cancellation

Long-running kernels must check `ctx.cancel_token.is_set()` periodically and abort early when it is set:

```python
for chunk in large_iterator:
    if ctx.cancel_token.is_set():
        return None, "Cancelled"
    process(chunk)
```

### Logging

Use `ctx.logger` rather than `print()`. Log output is captured and forwarded to the worker log:

```python
ctx.logger.info(f"Processing {file_path}")
ctx.logger.warning("Unexpected encoding — falling back to latin-1")
ctx.logger.error(f"Subprocess failed: {e}")
```

### Reading settings

Always read settings at call time, not at module import time:

```python
# CORRECT
def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    max_pages = ctx.settings.get("extractor", "pdf_max_pages")

# WRONG — value is frozen at import time, ignores runtime changes
MAX_PAGES = ctx.settings.get("extractor", "pdf_max_pages")  # ctx not available here
```

---

## 5. `IngestResult` Reference

The return envelope for a successful extraction.

| Field | Type | Description |
|-------|------|-------------|
| `text` | `str \| None` | Primary extracted text. Will be FTS5-indexed and chunked for embeddings. |
| `metadata` | `dict` | Structured key-value metadata (author, date, dimensions, codec, etc.). |
| `images` | `list[dict]` | Extracted images. Each dict has `path`, `caption`, `page_number`. |
| `child_tasks` | `list[dict]` | Files to dispatch as new ingestion tasks. Each dict has `file_path`, `source_hash`. |
| `enrichments` | `dict` | Extra payload merged into the Qdrant vector payload (searchable fields). |
| `errors` | `list[ExtractorError]` | Non-fatal errors (logged but do not fail the task). |
| `status` | `str` | `"ok"` / `"partial"` / `"empty"`. |
| `extractor_name` | `str` | Set automatically by the framework. Do not set manually. |
| `elapsed_secs` | `float` | Set automatically by the framework. Do not set manually. |

### Status values

| Value | When to use |
|-------|-------------|
| `"ok"` | Full extraction succeeded |
| `"partial"` | Some content extracted but parts failed (use `errors` to describe) |
| `"empty"` | File is valid but contains no extractable content |

### Minimal valid result

```python
IngestResult(text="Hello world", status="ok")
```

All fields other than `status` are optional and default to empty/`None`.

---

## 6. Certification Lifecycle

| Status | Meaning |
|--------|---------|
| `unverified` | Discovered on disk but not yet certified |
| `certified` | Passed contract audit; file hash stored for tamper detection |
| `tampered` | File hash changed since certification — automatically disabled, alert sent |
| `disabled` | Manually disabled via the Extractor Lab UI |

### Auto-certification

Built-in kernels with IDs starting with `com.docvault.*` or `com.microsoft.*` are certified automatically on startup.

### Manual certification

Third-party kernels are certified via the Extractor Lab UI (Certification panel) or the API:

```
POST /api/utils/certify/register
```

Certification runs two checks:

1. **Manifest check** — required keys present and correctly typed
2. **Signature check** — `extract()` has the exact required parameter annotations

Both checks must pass before a kernel is marked `certified`.

### Tamper detection

If a certified kernel's file is modified after certification, its SHA-256 hash no longer matches the stored value. On the next startup, DocVault:

1. Sets the kernel's status to `tampered`
2. Disables it (will not be routed any tasks)
3. Sends a critical alert via the alert bus

After intentional changes, re-certify the kernel through the Extractor Lab or the API to restore it to `certified` status.

---

## 7. Minimal Complete Example

```python
"""Minimal example kernel — handles .example files."""

from core.extractors.base import ExtractorContext, IngestResult

MANIFEST = {
    "id":         "com.example.minimal",
    "version":    "1.0.0",
    "name":       "Minimal Example Kernel",
    "extensions": [".example"],
    "requires":   [],
}


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return None, f"Failed to read file: {e}"

    if not text.strip():
        return IngestResult(text=None, status="empty"), None

    return IngestResult(text=text, metadata={"char_count": len(text)}, status="ok"), None
```

---

## 8. Quick Reference Checklist

Before submitting or certifying a kernel, verify:

- [ ] `MANIFEST` is a static dict literal with all four required keys
- [ ] `id` uses reverse-domain format and is globally unique
- [ ] `extensions` entries are lowercase and dot-prefixed (e.g. `".pdf"` not `"pdf"`)
- [ ] `extract(file_path: str, ctx: ExtractorContext) -> tuple` — exact signature
- [ ] Returns `(IngestResult, None)` on success, `(None, "message")` on failure
- [ ] No bare exceptions escape `extract()`
- [ ] Long loops check `ctx.cancel_token.is_set()`
- [ ] Uses `ctx.logger` instead of `print()`
- [ ] Reads settings via `ctx.settings.get()` at call time, not at import time
- [ ] `status` field set to `"ok"`, `"partial"`, or `"empty"`
