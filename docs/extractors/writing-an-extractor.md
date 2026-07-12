# Writing a DocVault Extractor Kernel

## 1. Introduction

This guide walks through building a complete, working extractor from scratch. By the end you will have a certified kernel that DocVault discovers automatically and routes to matching file types.

Before you start, read [contract.md](contract.md) — it defines the MANIFEST spec and `extract()` signature that your kernel must satisfy.

---

## 2. Worked example: TOML configuration file extractor

We will build `extractors/toml_extractor.py` step by step.

### Step 1 — Create the file

Create `extractors/toml_extractor.py`. Start with the module docstring and imports:

```python
"""TOML configuration file extractor."""
try:
    import tomllib          # Python 3.11+ standard library
except ImportError:
    import tomli as tomllib  # pip install tomli for Python < 3.11

from core.extractors.base import ExtractorContext, IngestResult
```

> **Note:** Import heavy dependencies inside `extract()` if they take >50 ms to load. `tomllib` is fast, so a top-level import is fine here.

### Step 2 — Write the MANIFEST

```python
MANIFEST = {
    "id":         "com.example.config.toml",
    "version":    "1.0.0",
    "name":       "TOML Config Extractor",
    "extensions": [".toml"],
    "requires":   [],        # tomllib is stdlib; tomli is a pip fallback
}
```

The registry reads this dict with a static AST analysis — no code is executed during discovery. Keep MANIFEST at module top-level and use only literal values (no expressions or function calls).

### Step 3 — Write extract()

```python
def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    try:
        with open(file_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        return None, f"Failed to parse TOML: {e}"

    # Flatten nested config into readable key: value lines
    lines = []

    def flatten(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                flatten(v, f"{prefix}{k}.")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                flatten(v, f"{prefix}{i}.")
        else:
            lines.append(f"{prefix.rstrip('.')}: {obj}")

    flatten(data)
    text = "\n".join(lines)

    result = IngestResult(
        text=text if text else None,
        metadata={
            "key_count":          len(lines),
            "top_level_sections": list(data.keys()),
        },
        status="success" if text else "failed",
    )
    return result, None
```

The function signature must be exactly `extract(file_path: str, ctx: ExtractorContext) -> tuple`. Return `(IngestResult, None)` on success or `(None, "error message")` on failure. Do not raise exceptions — catch them and return the error string.

### Step 4 — Test with test_kernel.py

```bash
# Audit the contract first
python tools/test_kernel.py toml_extractor --audit --pretty

# Run against a real file
python tools/test_kernel.py toml_extractor pyproject.toml --pretty

# Inspect the full extracted text
python tools/test_kernel.py toml_extractor pyproject.toml --full-text --pretty
```

Expected audit output: all checks pass (manifest ✓, signatures ✓).

If the audit fails, the error message tells you exactly which check failed and why.

### Step 5 — Certify in the Extractor Lab

Open http://localhost:8050/lab. Your new kernel appears in the sidebar with status `unverified`.

1. Click the kernel name to select it.
2. Click **Certify** in the Certification panel.
3. Status changes to `certified`.

Built-in kernels (`com.docvault.*`) are certified automatically on startup. For your own kernels, manual certification is required once. After that, the kernel stays certified until its file changes — a file change triggers tamper detection and resets the status to `unverified`.

### Step 6 — Verify routing

After certification (or server restart), check that `.toml` files are routed to your kernel:

- Drop a `.toml` file into your vault.
- Watch the Vault Log — the task should appear and reach `EMBEDDED`.
- Search for a key from the file to confirm it was indexed.

---

## 3. Do / Don't reference

### Do

- Check `ctx.cancel_token.is_set()` before long loops or expensive operations. Workers set this token when a task is killed; your extractor should exit promptly.
- Use `ctx.logger.info()` / `ctx.logger.warning()` instead of `print()`. Messages are written to `logs.db` and are visible in the Extractor Lab.
- Read settings via `ctx.settings.get('group', 'key')` inside `extract()`, never at module level. The settings resolver is not fully initialised at import time.
- Return `(None, "descriptive error message")` on failure — do not raise exceptions from `extract()`.
- Use `status="partial"` when some content was extracted but errors occurred. This signals to the worker that the result is usable but incomplete.
- Handle encoding errors gracefully: `open(..., errors="replace")` for text files avoids crashing on non-UTF-8 bytes.
- Call `ctx.report_progress(text, pct)` for long-running extractions (e.g. page-by-page PDF processing) so the Vault Log shows live progress.

### Don't

- Import heavy dependencies (`torch`, `cv2`, `mediapipe`) at module top-level. The registry performs AST-based manifest discovery without executing module code, but the import still runs when the module is loaded by the router. Lazy imports inside `extract()` keep startup instantaneous.
- Read settings at import time — the settings resolver is not ready yet.
- Write files to disk inside `extract()`. Use `IngestResult.images` (a list of `ExtractedImage`) to hand off extracted images to the worker; it handles file placement and downstream routing.
- Swallow exceptions silently. Report them via `IngestResult.errors` (append an `ExtractError`) or the error return value so operators can diagnose failures in the Extractor Lab.
- Hardcode file paths — use `ctx.settings.get()` for anything configurable.
- Return an `IngestResult` with `status="ok"` — the valid values are `"success"`, `"partial"`, `"failed"`, and `"cancelled"`. Use `"success"` for a clean extraction.

---

## 4. Child task dispatch

If your extractor finds embedded files (e.g., attachments inside a container format), dispatch them as child tasks rather than processing them inline. The worker creates new task records for each child; they are routed through the normal extraction pipeline.

```python
from core.extractors.base import ChildTask, IngestResult

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    # ... unpack the container, write attachments to a temp location ...

    child_tasks = [
        ChildTask(
            file_path="/path/to/extracted/attachment.jpg",
            file_type=".jpg",
            vault_id=ctx.vault_id,
            priority=10,
        ),
    ]

    result = IngestResult(
        text=main_text,
        child_tasks=child_tasks,
        status="success",
    )
    return result, None
```

The image above would be picked up by the image extractor (OCR), face analytics, etc., exactly as if it had been scanned from disk directly.

> **Note:** Write the extracted attachment to a path inside `.cache/` or a system temp directory. The ingestor will hash and import it on the next scan cycle, or the worker will queue it immediately if the child task record is created directly.

---

## 5. Heavy model loading

For kernels that load large models (Whisper, TensorFlow, PyTorch), always defer the import and cache the loaded model at module level so subsequent calls are free:

```python
# Bad — blocks discovery and slows startup
import torch
model = torch.load(...)

# Good — lazy import inside extract(), cached after first call
_model = None

def _get_model():
    global _model
    if _model is None:
        import torch
        _model = torch.load(...)   # runs once; cached for all future calls
    return _model

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    model = _get_model()
    # ...
```

First call loads the model (may take 30–120 seconds depending on size and hardware). Subsequent calls return the cached instance instantly. The `LazyPythonKernel` proxy in the router applies the same principle at the kernel level — the module is not imported at all until the first matching file arrives.
