# Project Status — 2026-07-19

Supersedes all earlier status documents.

---

## 1–28. Previous Work — COMPLETE

See `docs/internals/status/2026-05-31-project-status.md` for full details on:
Span Grounding, Embedding Throughput & Stability, Database Performance, OOM Prevention & Auto-Restart,
extracted_texts Table Migration, Streaming RAG & Search Mode, and all work referenced from earlier status docs
(Security Hardening, Prompt Injection Hardening, Archive X-Ray, LanceDB Migration, Sentence-Aware Chunker,
Pipeline Stall Sensors, Per-Vault Ignore Rules, Font Scale, Theme Editor, and more).

---

## 29. Qdrant Fully Removed, Art Enrichment Reliability — COMPLETE (2026-07-08–13, master, 4849c72 → 62176b2)

The 2026-05-31 doc noted Qdrant's offline-fallback code was *kept* in `search.py` "pending cleanup." That cleanup happened:

| Commit | Change |
|---|---|
| 4849c72 | `art_index` migrated from Qdrant to LanceDB — Qdrant removed entirely, including the fallback path. `qdrant-client` dropped from `requirements.txt`. |
| cb8a81f | Art enrichment worker surfaces the real Google Vision error body on cloud API failures (was swallowed), backs off on HTTP 403 instead of hammering a disabled/quota-exceeded API key. |
| ff3539e, 62176b2 | New `art_enrichment_issues` table (`docvault.db`) tracks failed/low-confidence identifications as a live worklist (not a `logs.db` history table — rows are added/removed as issues are reprocessed or dismissed). New Utilities-page panel: filter, multi-select reprocess/dismiss, dismiss-all, and a rescan action that backfills the table from existing `.nfo` sidecars. |

**Impact:** No more Qdrant/Docker dependency anywhere in the codebase (main store *and* art index both on LanceDB). Art enrichment failures are now visible and actionable from the UI instead of requiring a filesystem walk of `.nfo` files.

---

## 30. Windows Ollama Port-Exclusion Auto-Recovery — COMPLETE (2026-07-13, master, 8b97afd)

The 2026-05-31 doc's port-11434→11600 fix was a one-time manual move. It turned out Windows/Hyper-V re-randomizes its dynamic port-exclusion ranges on every reboot, so a port that worked for months could suddenly start refusing binds again with no obvious error (`start.ps1` launches Ollama hidden, so the underlying `bind: An attempt was made to access a socket...` error was easy to miss — only a 10s timeout was visible).

**Fix:** `start.ps1` now checks on every run whether the configured Ollama port is still free; if not, it picks a new free port, rewrites `config.ini`'s `[ollama] host`, and persists the new `OLLAMA_HOST`. It also clears any stale/locked native `ollama.exe` process before relaunching (the Windows Ollama app's single-instance lock could otherwise silently no-op a relaunch with no visible error). No manual intervention needed — just re-run `start.ps1`. Documented in `docs/troubleshooting.md`.

---

## 31. Claude API Key & Model as Settings — COMPLETE (2026-07-18, master, aec6c59 → 9683116)

`llm/factory.py`'s `claude` provider branch bypassed the settings system entirely — it opened a fresh `configparser` and read `config.ini` directly for the API key, which crashed on first use (`NoOptionError`) since no such key ever existed anywhere. Selecting `llm:provider = claude` was effectively unusable.

| Task | Change |
|---|---|
| 1 | Added `llm:api_key` and `llm:claude_model` (default `claude-sonnet-5`, replacing the stale/invalid `claude-sonnet-4-6`) to the settings schema, group `general`. Rewrote `factory.py`'s claude branch to use `settings.get()`, matching the `ollama` branch's existing pattern. Both settings now appear in the Settings UI automatically (schema-driven page, no frontend changes needed). |
| 2 | Final review found `ClaudeProvider` had no `chat_stream` — the streaming Ask endpoint (`/query/stream`, what the UI's Ask panel actually calls) would `AttributeError` with Claude selected, even though the non-streaming `/query` endpoint worked. Added `chat_stream` via Anthropic's SSE streaming API (`httpx.stream`), matching `OllamaProvider.chat_stream`'s exact contract: synchronous generator, errors yielded as a string rather than raised, so `query.py`'s consumer needed no changes. |

A task-review finding (URL/headers/payload construction duplicated between `chat()` and `chat_stream()`) was fixed by extracting shared `_headers()`/`_payload()` helpers.

**Design spec:** `docs/superpowers/specs/2026-07-18-claude-api-key-settings-design.md`
**Plan:** `docs/superpowers/plans/2026-07-18-claude-api-key-settings.md`

---

## 32. Deleted-File Detection — COMPLETE (2026-07-19, master, d941d25 → 2160bd0)

DocVault previously had no way to detect a file removed from disk — its `tasks` row, extracted text, FTS entry, and vector embedding stayed forever, silently pointing at nothing.

### What Was Built

| Component | Description |
|---|---|
| Schema | `file_vault.miss_count` (per vault-membership absence counter), `tasks.pre_missing_status` (for restore), new setting `ingestion:missing_after_scans` (default 3). |
| Detection (`core/ingestor.py`) | After each vault scan, reconcile which known files were/weren't seen. A task flips to a new `tasks.status = 'MISSING'` only once **every** vault registration of that `file_hash` has crossed the miss threshold — content shared across multiple vaults (via `file_vault`) stays visible while any copy still exists. Nothing is deleted at this stage. |
| Restore | A file reappearing resets `miss_count` and restores `tasks.status` from `pre_missing_status` — with a safety mapping (`PROCESSING → PENDING`, `EMBEDDING → EXTRACTED`) so a file that vanished mid-processing doesn't resume a stale in-flight state no worker still owns. |
| Purge | `core/manager.py::purge_file_hashes` — a shared hard-delete helper extracted from `VaultManager._gut_vault`'s existing batch-delete logic (which now delegates to it). `purge_missing_files`/`purge_missing_files_by_filter` always re-verify `status = 'MISSING'` server-side before deleting — a caller-supplied hash list is never trusted blindly. New `POST /catalog/missing/purge` endpoint; listing reuses the existing `GET /api/catalog?status=MISSING` (no new list endpoint needed). |
| Search exclusion | FTS/filename search: plain SQL `status != 'MISSING'` clause. Semantic/RAG search: deliberately **not** a LanceDB hash filter (would repeat the giant-`IN`-clause performance bug fixed in `9522b5e`) — instead fetches the small MISSING-hash set separately and post-filters already-returned ANN candidates in Python (`search/semantic.py`). Vault Log/catalog browsing is unchanged — `MISSING` is just another visible status there, useful for diagnosis. |
| UI | New "Missing Files" accordion panel in `frontend/vault.html`, modeled on the Art Enrichment Issues panel — filter by vault/filename, multi-select purge. |

### Three real bugs caught by review, not present in the original plan

1. **Folder-ignore false positive:** `ingestion:ignore_folders` patterns pruned `os.walk`'s traversal before file paths were ever recorded as "seen" — adding a folder to the ignore list would make its still-present files drift toward `MISSING`. Fixed by walking pruned folders for enumeration-only path tracking before excluding them.
2. **Latent `_gut_vault` FK bug:** its delete order (`tasks` before `file_vault`) violated `file_vault.file_hash REFERENCES tasks(file_hash)` under `PRAGMA foreign_keys=ON` — a bug present since `_gut_vault` was written, never triggered because no prior test exercised it with populated `file_vault` rows. Fixed by deleting `file_vault` before `tasks` in the now-shared `purge_file_hashes`.
3. **Reconciliation write amplification:** the detection pass originally opened one SQLite connection + commit per absent file, every scan cycle — a mass deletion (e.g. a 20K-file folder) meant thousands of write-lock acquisitions per cycle, contending with extraction/embedding workers. `miss_count` also grew unboundedly forever for `MISSING`-but-unpurged files. Fixed to do the whole per-vault reconciliation in one transaction, and to stop incrementing once a hash's task is already `MISSING`.

**Design spec:** `docs/superpowers/specs/2026-07-19-deleted-file-detection-design.md`
**Plan:** `docs/superpowers/plans/2026-07-19-deleted-file-detection.md`

---

## 33. Current Backlog (supersedes §24 of the 2026-05-31 doc)

### Completed & Released (since 2026-05-31)
- ✅ Qdrant fully removed (art_index → LanceDB) — MERGED (4849c72, 2026-07-08)
- ✅ Art enrichment error tracking + management UI — MERGED (62176b2, 2026-07-13)
- ✅ Windows Ollama port-exclusion auto-recovery — MERGED (8b97afd, 2026-07-13)
- ✅ Claude API key/model as settings + streaming support — MERGED (9683116, 2026-07-18)
- ✅ Deleted-file detection — MERGED (2160bd0, 2026-07-19)

### Correction to prior backlog
- **"New vault wizard"** — investigated 2026-07-18: `frontend/vault.html` already has a 3-tab Add Vault modal (Identity/Kernels/Maintenance). Removing from backlog; if something specific is still missing, needs re-scoping with the user rather than a fresh build.

### In Progress / Pending (unchanged)
- **Face Crop Gallery UI** — in Identity Hub; low priority
- **Unified Settings** — the Claude API key fix (§31) closed one specific instance of this, but the general "kernel-specific env vars belong in the Settings UI" backlog item is broader and still open; requires a settings-refactor audit of remaining env-var reads outside `settings.get()`
- **"Nerds" diagnostics page** — logs.db dashboard; awaiting logs.db adoption

### Art Collection Intelligence (unchanged since 2026-05-31, Tier 3 error-tracking now shipped per §29)
- **Tier 1:** `art_collection_extractor.py` — folder kernel, structural summary, child task queuing (planned)
- **Tier 2:** Image child tasks → idle Ollama vision worker (descriptions) (planned)
- **Tier 3:** `art_enrichment_worker.py` + Google Vision API + `.nfo` sidecars + transactional rename — **built and in active use**; error tracking/review UI now shipped (§29)

### Other Backlog (unchanged)
- **Settings UI:** deleted file detection ✅ done (§32); new-vault wizard (see correction above); Claude API key ✅ done (§31)
- **Vault inheritance:** per-vault ignore rules → vault-aware schema + Settings resolver already done
- **Performance:** write serialisation to reduce lock contention (deferred)

---

## 34. Known Issues (supersedes §25 of the 2026-05-31 doc)

- **WMI CPU temp sensor** — fails on some machines with COM error 0x80041003. `tools/test_wmi_temp.py` created to diagnose. Monitor falls back to dummy (0°C). Non-critical.
- **SQLite lock contention** — `apply-ignore` mitigated with two-pass approach. Workers doing long write transactions can still block short API writes. Full fix deferred (write serialisation, §33).
- **Qdrant fallback path — REMOVED** (was listed as "kept as backup pending cleanup" in the 2026-05-31 doc; superseded by §29 — Qdrant is now fully gone, not just unused).
