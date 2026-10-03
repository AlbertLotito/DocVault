# UNKNOWN Status Fix, Email Extractor, Audacity Cleanup — Design

**Date:** 2026-10-03 · **Status:** Approved in chat

## Problem
68,680 tasks are in ERROR, about 35% of the library. 63,767 of them (93%) aren't failures: they're files whose type has no extractor (1,276 types), reported by the fallback kernel as "Unsupported format". The real failures (~4,900) and a large missing feature are buried in that noise:
- **23,124 Thunderbird emails** (`.wdseml`, plain RFC-822) are unsearchable.
- **3,958 `.au` "audio" errors:** every one of the 3,367 `.au` files is an Audacity project block file, not playable audio.

## Decisions
| | Decision |
|---|---|
| Unsupported types | Use the existing **`UNKNOWN`** status (already wired into stats, the Vault filter and tile, the unknown-types list, and telemetry). It was approved in chat as "SKIPPED"; same meaning, existing name. |
| Email | New extractor for `.eml` + `.wdseml`. `.msg`/`.mbox` are out of scope. |
| Code/config/SDK types (`.h`, `.pyi`, `.pm`, …) | Stay UNKNOWN. Third-party code is search noise. |
| Audacity | `.au` extension ignore rule on the Documents vault; purge the 3,367 rows. |
| Remaining ~1,500 real failures | Next round. |

## 1. UNKNOWN status
- **Root bug:** `extraction_worker.process_task` checks `extractors == [fallback_kernel]` against the *module*, but the router returns a `LazyPythonKernel` proxy. The branch never fires, so every unroutable file runs the fallback kernel and becomes ERROR. The unit test mocked the router with the module, so it passed.
- **Fix:** compare with `router.FALLBACK_KERNEL`, the proxy the router actually returns. When there's no route, the worker marks the task `UNKNOWN` ("No extractor for .xyz files") and runs nothing. A separate `has_route()` lookup was tried and dropped: it made tests that mock `get_extractors` initialise the real router.
- **Re-queue:** at the end of `router.reload()` (startup, Lab activate, hot reload), `manager.requeue_unknown(routed_extensions)` resets UNKNOWN tasks whose type now has a route back to PENDING. Best-effort: a failure is logged and the reload still succeeds.
- **Migration** (idempotent, in `init_db`): `ERROR` rows whose `error_log` starts with `[fallback_kernel]` become `UNKNOWN`.

## 2. Email extractor (`extractors/email_extractor.py`)
- **Extensions:** `eml`, `wdseml`. Parsed with stdlib `email` (`policy.default`), which decodes RFC 2047 headers and multipart messages.
- **Text:** a header block (From / To / Cc / Subject / Date), a blank line, then the body.
  - The body is the first `text/plain` part that isn't an attachment, else the first `text/html` part rendered with `html_extractor._render`.
  - If there are attachments, an `Attachments: a.pdf, b.jpg` line follows.
- **Metadata:** `from`, `to`, `cc`, `subject`, `date` (ISO 8601 when parseable), `message_id`, `attachments`.
- **Errors:** a file that isn't a MIME message (no From/Subject/Date headers and no body) returns an error. A message with headers but an empty body still indexes its headers.

## 3. Audacity
Add `au` to the Documents vault's `ignore_extensions`, then `purge_file_hashes` the 3,367 `.au` tasks and delete their (absent) vectors.

## Rollout
Server stopped → certify the email kernel → migrate → restart, which re-queues the `.wdseml` files through the reload hook → verify counts and run a live email search through the MCP server.

## Tests
- Worker: production-shaped router (a proxy object, not the module) yields UNKNOWN, and the fallback never runs.
- `requeue_unknown` resets only routed types; the reload hook calls it.
- Migration: only the fallback-kernel ERRORs are converted.
- Email: plain-text, HTML-only and multipart-with-attachment messages, encoded headers, a header-only message, a non-email file, and real `.wdseml` samples.
