# test_kernel — Kernel Test Adapter

`tools/test_kernel.py` is a standalone CLI for running and auditing DocVault extractors without starting the server. It loads kernels in isolation, executes them against real files, and reports structured results that are easy to inspect by hand or parse programmatically.

## Quick start

```bash
# Run an extractor against a file
python tools/test_kernel.py plaintext_extractor E:/docs/readme.txt --pretty

# List all discoverable kernels
python tools/test_kernel.py --list --pretty

# Check a kernel's contract without running it
python tools/test_kernel.py source_code_intelligence_extractor --audit --pretty
```

## Modes

### `--list` — Discover kernels

Scans `extractors/` and prints every discoverable kernel. No kernel name required.

```bash
python tools/test_kernel.py --list
python tools/test_kernel.py --list --pretty
```

Output includes the kernel name, version, and supported extensions. No code is executed — discovery uses AST inspection only.

---

### `--info` — Inspect manifest

Reads and displays a kernel's `MANIFEST` dict without executing any code.

```bash
python tools/test_kernel.py face_analytics_extractor --info
python tools/test_kernel.py face_analytics_extractor --info --pretty
```

Useful for quickly checking what extensions a kernel claims to handle, its declared dependencies, and its `target_type` (`file` or `folder`).

---

### `--audit` — Contract audit

Verifies a kernel satisfies the DocVault extractor contract:

- `manifest` check — `MANIFEST` dict is present and has required keys (`id`, `version`, `name`, `extensions`)
- `signatures` check — `extract()` function is present with correct type annotations (`file_path: str`, `ctx: ExtractorContext`)

```bash
python tools/test_kernel.py source_code_intelligence_extractor --audit
python tools/test_kernel.py source_code_intelligence_extractor --audit --pretty
```

Exit code is `5` if any check fails, `0` if all pass.

---

### Run test (default mode)

Runs a kernel against one or more files and reports the result.

```bash
# Single file
python tools/test_kernel.py plaintext_extractor E:/docs/note.txt

# Multiple files — outputs a JSON array, worst exit code wins
python tools/test_kernel.py plaintext_extractor E:/a.txt E:/b.txt E:/c.txt

# Human-readable output
python tools/test_kernel.py text_extractor E:/docs/report.pdf --pretty
```

By default, a pre-flight contract audit runs before execution. Skip it with `--skip-audit` if you want raw extraction speed.

A live elapsed-time spinner displays on stderr while the kernel runs. It disappears cleanly when the run completes, leaving stdout with only the JSON result.

## Flags reference

| Flag | Default | Description |
|------|---------|-------------|
| `--pretty` | off | Human-readable terminal output instead of JSON |
| `--full-text` | off | Include the complete extracted text in output |
| `--no-text` | off | Omit text entirely (metadata and stats only) |
| `--text-limit N` | 500 | Preview character limit when not using `--full-text` |
| `--timeout SECS` | 300 | Kill the extraction thread after this many seconds |
| `--vault VAULT_ID` | none | Pass a vault context to the kernel (affects settings resolution) |
| `--skip-audit` | off | Skip the pre-flight contract audit |
| `--output FILE` | none | Also write the JSON result to a file |
| `--quiet` | off | No spinner; plain progress lines on stderr (for CI / piped use) |

## Output structure

Every run produces a JSON object (or array of objects for multiple files):

```json
{
  "kernel":        "plaintext_extractor",
  "file":          "E:\\docs\\note.txt",
  "file_size":     1234,
  "file_ext":      "txt",
  "status":        "pass",
  "elapsed_secs":  0.042,
  "load_secs":     0.011,
  "text_chars":    1234,
  "text_lines":    40,
  "text_preview":  "First 500 chars of extracted text...",
  "full_text":     null,
  "metadata":      {},
  "image_count":   0,
  "child_tasks":   0,
  "error_count":   0,
  "errors":        [],
  "traceback":     null,
  "manifest":      { "id": "com.docvault.text.plain", ... },
  "audit":         { "passed": true, "checks": [...] },
  "notes":         []
}
```

Key fields:

- **`status`** — `pass`, `fail`, `timeout`, `crash`, `error`, `audit_fail`
- **`elapsed_secs`** — total wall time including kernel load
- **`load_secs`** — time to import the module only (useful for diagnosing first-run model loading)
- **`notes`** — diagnostic observations (e.g. slow load warning, cancellation notice)
- **`errors`** — extractor-reported errors (not Python exceptions); each has `extractor`, `type`, `message`
- **`traceback`** — Python traceback on `crash` status

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | pass — extraction completed, content returned |
| `1` | fail — ran but returned no content, or extractor reported errors |
| `2` | timeout — did not complete within `--timeout` seconds |
| `3` | crash — unhandled Python exception inside the extractor |
| `4` | usage — bad arguments, file not found, kernel not found |
| `5` | audit — contract audit failed |

For multiple files, the exit code is the worst code across all runs.

## Behaviour notes

**No server required.** The tool bootstraps the repo root onto `sys.path` and imports DocVault's core modules directly.

**Settings fallback.** If `settings.db` is absent (e.g. a fresh checkout), the tool falls back to schema defaults so kernels that read settings at call time still work.

**Lazy import kernels.** Kernels that defer heavy imports (`cv2`, `mediapipe`, `torch`) to their `extract()` function will import cleanly under `--audit` and `--info`. They only load their heavy deps when actually run.

**First-run AI model loading.** Kernels like `aural_intelligence_extractor` (Whisper) or `face_analytics_extractor` (FER/TensorFlow) may take 30–120 seconds on first use while their models are downloaded or compiled. The spinner shows elapsed time so you can see it is progressing. Use `--timeout 600` if needed.

**Typo correction.** If you mistype a kernel name, the tool suggests close matches using difflib:

```
Error: Kernel not found: face_analytic.  Did you mean: face_analytics_extractor?
```

**`.py` suffix stripping.** Passing `face_analytics_extractor.py` is handled — the `.py` is stripped automatically.

**Framework noise suppression.** `TF_CPP_MIN_LOG_LEVEL`, `ONEDNN_VERBOSE`, `MEDIAPIPE_DISABLE_GPU`, and `TOKENIZERS_PARALLELISM` are all silenced before any imports land.

## Typical development workflow

```bash
# 1. Write / edit your extractor
# 2. Verify the contract
python tools/test_kernel.py my_extractor --audit --pretty

# 3. Run against a sample file
python tools/test_kernel.py my_extractor E:/samples/test.pdf --pretty

# 4. Inspect the full text output
python tools/test_kernel.py my_extractor E:/samples/test.pdf --full-text --pretty

# 5. Save the result for diffing across iterations
python tools/test_kernel.py my_extractor E:/samples/test.pdf --output before.json
# (make changes)
python tools/test_kernel.py my_extractor E:/samples/test.pdf --output after.json

# 6. Batch test across a folder of sample files
python tools/test_kernel.py my_extractor E:/samples/*.pdf --no-text --pretty
```

## Machine-readable / Claude use

Omit `--pretty` to get clean JSON on stdout. Pipe stderr to `/dev/null` (Unix) or `2>nul` (Windows) to suppress the spinner:

```bash
python tools/test_kernel.py plaintext_extractor E:/note.txt 2>/dev/null
```

The JSON output is designed to be unambiguous — all durations are in seconds as floats, all counts are integers, status is always one of the six named strings above.
