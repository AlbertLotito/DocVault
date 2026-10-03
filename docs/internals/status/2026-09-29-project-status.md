# Project Status — 2026-09-29

Supersedes all earlier status documents.

---

## 1–41. Previous Work — COMPLETE

See `docs/internals/status/2026-08-02-project-status.md` for §37–41 (Windows autostart + system tray, tray search popup, stale router/video-extractor tests, `Moved:` log noise), and `docs/internals/status/2026-08-01-project-status.md` for everything before that.

---

## 42. E: Drive Fully Retired (2026-08-30, master, 97ef6b5, cb15ce7)

The old external `E:\DocVault` drive (the flaky USB dock from §35) has since failed and can't be read. The planned "rename it to `.bak`" cleanup no longer applies: `D:\DocVault` is now the only copy.

- `97ef6b5`: CLAUDE.md memory-file paths and project location moved to `D:\DocVault`.
- `cb15ce7`: `setup.ps1`/`reset.ps1` derive their paths from the script's own location instead of a hardcoded `E:\DocVault\...`. These fixes had been sitting uncommitted since the original 2026-07-26 migration session.

---

## 43. Purge-All Missing Files Silently Did Nothing (2026-08-30, master, 676d194)

The Missing Files panel's purge-all button used `window.confirm()`. Once Chrome/Edge's "prevent additional dialogs" has been tripped, that call returns `false` with no visible UI, so the button looked completely broken. Fixes:

- New shared `lcConfirm()` modal in `lcars.js`, which the browser can't silence. Purge now also shows a disabled "PURGING..." state, so a long purge doesn't look like a no-op.
- `VectorStore.delete_by_hashes()` put every purged hash into one `IN (...)` clause. This is the same multi-megabyte-string hang already fixed for `search()`, and it's now batched the same way.
- Blanket no-cache middleware in `api/main.py`. `FileResponse`/`StaticFiles` sent no `Cache-Control`, so browsers could keep serving stale `vault.html`/`lcars.js` across plain reloads.

---

## 44. Repo Hygiene + Quick-Start Guide (2026-09-13, master, dca54d0, c09244d, fd1b4bb)

- `dca54d0`: `config.ini` is no longer tracked (it's per-machine output written by `setup.ps1`); `config.ini.example` is the tracked template. The stray `GeminiReview.md` moved to `docs/internals/plans/2026-03-04-gemini-review.md` and is marked historical.
- `c09244d`: new `QUICKSTART.md`; the real repo URL replaces the `your-org/docvault` placeholder; the README points no-GPU users at the SideQuests §4 gap analysis. **This commit also documented a Browse page that doesn't work yet. See §48.**
- `fd1b4bb`: personal email redacted from the Gmail-alerts planning doc.

---

## 45. Ollama Outage → Self-Heal Guard (2026-09-21, master, 7b59d3e)

**Symptom:** RAG answered `[LLM error: [WinError 10061] ... actively refused it]`.

**Root cause:** the Ollama tray app (`ollama app.exe`) updated itself from 0.34.1 to 0.34.2 with `/FORCECLOSEAPPLICATIONS`. That killed every `ollama.exe`, including the `ollama serve` that `start.ps1` had launched on `127.0.0.1:11600`. The app then relaunched its own server on `0.0.0.0:11434`, which ignores our `OLLAMA_HOST`. `start.ps1` only checks Ollama at startup, so nothing recovered.

**Fix:** `core/ollama_guard.py`, a daemon thread started in `run.py` beside the monitor. It acts only when `llm:provider=ollama` and the host is local. It needs 2 consecutive probes that are *refused* (a timeout means busy and never triggers a restart). It waits 300s between attempts and relaunches on `127.0.0.1:<port from ollama:host>`, never `0.0.0.0`. It sends a `warning` alert on success and an `error` alert on failure through `core/alerts.py`. New settings: `ollama:guard_enabled`, `ollama:guard_interval` (30s). Tests: 15 in `tests/test_ollama_guard.py`. Verified live: after killing Ollama, the guard revived it on the configured port.

**Known gaps:**
- A shift in Windows' excluded port ranges is still `start.ps1`'s job.
- The tray app's own server binds `0.0.0.0:11434`, which exposes Ollama unauthenticated on the LAN whenever that server is running.

---

## 46. `start.ps1` Moved Ollama's Port Up by One on Every Restart (2026-09-21, master, a15cdbc)

`Test-PortFree` treated *any* connection on the port as "in use", including the healthy Ollama already listening there. Each DocVault restart with Ollama up therefore picked a new port (11600→11601→…), rewrote `config.ini` and the User `OLLAMA_HOST`, and killed and relaunched Ollama. This is where the stray `11601` in `config.ini` came from.

**Fix:** `Find-FreeOllamaPort` keeps the preferred port when `/api/version` already answers there. The port helpers moved into `ollama_port.ps1`, which `start.ps1` dot-sources, so **the two files must stay side by side**. Tests: `tests/test_start_port_selection.py` runs the real functions under `powershell.exe` 5.1 against real sockets. Verified with a full restart: the Ollama PID, `config.ini` and the env var were all unchanged.

**Unexplained, parked:** during the 2026-09-21 test restarts, the server exited cleanly (code 0) about 2 minutes after the first restart. Only `/utils/shutdown` or a console signal can do that. It didn't happen on later restarts.

---

## 47. Optimizer-Snapshot Rollback Bug + All Pre-Existing Test Failures Fixed (2026-09-29, master, 496b134, d5acb3d)

- `496b134`: `run.py` called `logger.warning`, which `core.logger` doesn't have (the API is `debug/info/warn/error/critical`). Both success paths of `_rollback_optimizer_snapshot()` raised `AttributeError` *after* the rollback had committed, and the outer except logged "optimizer snapshot rollback failed". The rollback itself worked; only the log message was wrong. Fixed, with 3 regression tests (`tests/test_optimizer_snapshot_rollback.py`) that fail on the old code.
- `d5acb3d`: the failures listed in §39 since 2026-08-02. All were stale tests except the i18n gap:
  - `test_embedding_worker` ×3: `process_task_batch` now does one `vs.upsert_documents()` merge for the whole batch instead of calling `upsert_batch()` per document. The tests were updated to match.
  - `test_embed_throughput_settings`: the `workers:embed_batch_size` default is 16 (on purpose), not 8.
  - `test_search_throttle`: failed only when run in the full suite. An earlier search-API test called `notify_user_activity()`, which left the 60s search throttle active. The test now resets `_last_user_activity` for each test.
  - `test_i18n`: `es.json`/`fr.json` were missing the three `vault.missing.*` keys added with the Missing Files panel. Added.

**Test status: full suite 447 passed, 0 failures.**

---

## 48. Untracked Files Integrated: EPUB/SVG Extractors + Browse Page (2026-09-29, master, ca146dc, 2977912)

Three files had sat untracked since late August / mid-September. All three are now committed and meet the system's standards.

**EPUB and SVG extractors (`ca146dc`, both now v1.1.0).** Both had been certified and live since 2026-08-30 (2,215 EPUBs and 55 SVGs processed) but were never committed.
- **EPUB:** now walks the spine in reading order, as its docstring claimed. It had been iterating manifest order, which can differ from reading order and includes the navigation (TOC) document. Checked against 150 real library EPUBs: same text volume, no new errors. The 22 existing EPUB errors are image-only or malformed books; none of them extract under either version.
- **SVG:** rasterizes at 300 dpi, the same convention as PDF OCR, with the longest side capped at 4000 px. svglib's 72 dpi default rendered SVGs at 75% of their declared size, so a 24 px icon became 18 px and vision (64 px minimum) skipped it entirely.
- 13 new tests. `EbookLib`/`lxml`/`svglib`/`reportlab` are now in `requirements.txt`; they were installed locally but never listed.
- **Re-certified after the edits.** The registry treats a certified kernel whose file hash changed as tampered and disables it at the next startup. Both were decertified and re-certified with the server stopped; the Lab contract audit passed. **Any future edit to an extractor needs the same re-certify step (Lab → Activate).**

**Browse page (`2977912`).** `frontend/browse.html` had no backend, even though README/QUICKSTART pointed users to it.
- `GET /api/catalog/tree`: one level of a vault's tree from `file_vault` paths, with recursive per-status folder counts. It's multi-vault safe, rejects paths outside the vault with 400, and escapes `_`/`%` in LIKE patterns.
- `POST /api/catalog/{hash}/scan_now`: PENDING/ERROR tasks get `priority = manager.SCAN_NOW_PRIORITY` and are claimed next; ERROR tasks are reset like Reprocess; other statuses return 409.
- `/browse` route and nav pill.
- The page now follows the house style: full en/es/fr i18n, the shared Inspect modal, escaped dynamic text, label contrast per the UI rule, and `--font-scale` support.
- New guard test: every i18n key used by any page must exist in `en.json`. It also caught `search.ask.save`, which was rendering as its raw key name.
- Documented in `docs/architecture.md` §5 and §9.

**Live-verified** against the 155,615-file vault. `/browse` returns 200. Tree counts reconcile exactly with `file_vault`. The root takes about 1.25s warm (2.7s cold right after a restart); the largest subfolder (42k files) takes under 0.3s. The floor is about 155k task lookups: a SQL-side `GROUP BY` and a covering `(file_hash, status)` index were both measured and gave no improvement. **Not visually checked in a browser** (Chrome extension not connected); the page script was syntax-checked with node.

Full suite: 476 passed.

**Data note, not a bug:** in the tree, `outlookdata` shows 22,095 ERROR out of 22,136 files, and `NAS Home` shows 13,934 ERROR. Worth a look someday: likely unsupported Outlook formats.

---

## 49. Known Issues

- **HTML files indexed as raw markup.** Hurts snippet quality (see §50).
- **35% of tasks in ERROR.** Needs triage (see §50).

- **Deferred tray-popup polish.** The minor, non-blocking items from §38 are still open.
- **Unexplained clean exit on 2026-09-21.** See §46; parked.
- WMI CPU temp and SQLite lock contention are still closed as non-issues (see the 2026-08-02 doc §39).

---

## 50. MCP Server for Chat Bots (Dudeskie) — COMPLETE (2026-10-03, master, 102e7c5 → docs commit)

The user's chat bot, **Dudeskie**, runs on this PC, uses Ollama, and is an MCP client. It needed to search DocVault through a self-describing contract. FastAPI's `/openapi.json` was unsuitable: it covers all ~100 routes, including purge and shutdown. Design brainstormed and spec'd in `docs/superpowers/specs/2026-10-03-docvault-mcp-server-design.md`.

**Built:**
- **`mcp_server/docvault_mcp.py`**, a standalone **stdio** MCP server on the `mcp` 2.x SDK (`MCPServer`). It's a thin async client over the existing REST API, so nothing changed in the server. Tools, all annotated read-only:
  - `docvault_search`: hybrid / fulltext / semantic / filename; vault by name or id; type and date filters
  - `docvault_get_document`: by hash or path; paged text
  - `docvault_status`: counts including MISSING, vaults, workers, index, issues
  - `docvault_ask`: DocVault's RAG; only published with `DOCVAULT_MCP_ENABLE_ASK=1`, since Dudeskie has its own LLM on the same GPU
- **The contract:** the `tools/list` schemas plus the server `instructions`.
- **`core/server_url.py`:** `get_server_url()` moved out of the tray into a dependency-free module, because stdout is the JSON-RPC channel and nothing that prints may be imported. **Latent bug avoided:** the tray runs as `pythonw tray/tray_app.py` with only `tray/` on `sys.path`, so importing `core.*` would have killed it silently at logon. It now adds the project root, and a test imports it exactly that way.
- **Docs:** `docs/mcp.md` (tool reference + client config), a README feature line, `docs/architecture.md` §10.

**Tests:**
- 31 new: 27 MCP tests + 4 for `server_url`, plus the tray script-launch guard.
- The MCP tests use a real in-process MCP client against a fake API that only accepts real `/api/` routes. The `/api/` check matters because unprefixed `/search` is a real route too (the HTML page). Mutation-checked: dropping `/api` from one call fails 7 tests.
- One test runs a real stdio subprocess handshake.
- Full suite: 508 passed.

**Live-verified over real stdio against the 195k-task vault:**
- Every tool works, including the error paths.
- Timings: status 1.0s, hybrid 1.5s, fulltext 0.3s, semantic 1.8s, filename 0.4s, get_document 0.5s, get-by-path 1.0s (was 6.2s before switching to the catalog lookup), ask 11s.

**Client config:**
```json
"docvault": {"command": "D:/DocVault/venv/Scripts/python.exe",
             "args": ["D:/DocVault/mcp_server/docvault_mcp.py"], "env": {}}
```
Use the full venv interpreter path. A bare `python` is the Windows Store stub here and hangs.

**Findings for later (not fixed):**
- **HTML files are indexed as raw markup.** Snippets look like `ign="left"/></span><span class=...`, which hurts both bot and human search quality.
- **35% of tasks are in ERROR** (68,668 of 195,310), including `outlookdata` at 22,095 of 22,136.

---

## 51. HTML Extraction, Text Extractor Re-Enabled, Two Performance Bugs, Vector Store Compaction (2026-10-03, master, 5d7f9d4 → 2ca16a5)

Found while building the MCP server (§50): HTML search snippets were raw markup. Fixing that and reprocessing the files exposed two long-standing performance bugs that capped the whole pipeline.

**HTML was indexed as raw markup (`5d7f9d4`, `2ca16a5`).**
- **Cause:** DocVault had no HTML-aware extractor. `.html` went to the source-code kernel plus the plaintext kernel, and the worker *chains* every kernel claiming an extension, so stored text was raw source plus a code report. 1,755 of 1,801 HTML/HTM files were affected. `.htm` / `.xhtml` / `.mht` / `.mhtml` weren't routed at all.
- **New `html_extractor`** (lxml):
  - strips head, script, style and comments
  - keeps block and line structure
  - honours the declared charset (Latin-1 decoded as cp1252)
  - title and description become metadata
  - unpacks MIME archives (`.mht`, and old mail saved as `.htm`)
  - a page with only a title indexes the title
- `html` removed from the plaintext and code kernels; a test enforces that only one kernel claims HTML types.
- **Results:** 1,777 HTML-family files reprocessed to clean text (about 12% of the old size). 37 are genuine ERRORs: pages with no text and no title. All 12 `.mht` files that used to fail now extract.

**`plaintext_extractor` re-enabled.** It had been decertified (state `unverified`, disabled) at some point after 2026-08-30, so new `.txt/.md/.csv/.json/.xml/.log` files failed as "Unsupported". The 11 that failed this way have been reprocessed.

**Stranded-EXTRACTED bug (`2ca16a5`).** A metadata-only extraction result was marked EXTRACTED, but the embedder only claims EXTRACTED tasks *with text*, so such a task could never leave that state. 43 pages hit it. Metadata-only results now go straight to COMPLETED.

**Performance bug 1: every FTS delete scanned the whole index (`a534513`).**
- **Cause:** `fts_index.file_hash` is `UNINDEXED` (FTS5), so `DELETE … WHERE file_hash = ?` read all 4.25M rows. That's 6.7s, paid on every completed extraction, capping extraction at about 650 files/hour for all file types.
- **Fix:** a new `fts_spans` table holds each file's rowid range, and every FTS write goes through `fts_insert()` / `fts_delete()`. The delete takes **36 ms**. One-time backfill: 40s.
- **Measured after the fix:** 160 extractions/minute, against about 11 before.

**Performance bug 2: the vector store was never compacted (`f6b4103`).**
- **Cause:** every embedding batch appends a fragment. The live table had 52,263 fragments (median 5 rows) and 12,000+ versions, with no index on the merge key `id`, so each upsert scanned everything and one batch stalled for over 20 minutes.
- **One-off compaction** (server stopped; 62.3 GB backup at `D:\DocVault-backups\lancedb_storage-20261003`, verified 87,644 files and 0 failures):
  - 52,263 → 3 fragments, all 1,300,493 rows preserved
  - sampled top-10 searches identical before and after
  - BTREE indexes on `id` and `file_hash`
  - upsert 20+ min → 2.4s
  - `lancedb_storage` 62 GB → 4.5 GB
  - `optimize()` took 26 min, mostly pruning 12k versions (about 58 GB of manifests to read)
- **Recurrence prevented:** the embedding worker compacts every `lancedb:optimize_interval_hours` (default 6, 0 = off). It runs synchronously between batches, under `_index_lock`.
- The old docstring note that `optimize()` "crashes under load" did not reproduce with no concurrent writers.

**Test isolation (`98da5e0`).** The suite was running deletes against the *real* `lancedb_storage` (`test_vault_ignores`, `file_hash='ddeeff…'`). They were harmless no-ops, but added versions and could race a compaction. An autouse conftest fixture now redirects both vector stores to a temp dir, and a guard test enforces it.

**`LocationHistory.json` excluded.**
- A 326 MB Google Takeout dump held 466k FTS rows, 11% of the full-text index. It had no vectors.
- A `Location History` folder ignore rule was added on the Documents vault. That row and a sibling `LocationHistory.kml` (ERROR) were purged.

**Noted, not fixed:**
- Per-vault kernel lists (`vault:extractor_config` in settings.db) are never enforced: `router.get_extractors()` ignores `vault_id`.
- `_rebuild_index` still rebuilds the full IVF-PQ index every 100 batches.

**Current totals:** 86,800 COMPLETED, 68,680 ERROR, 39,828 MISSING. Full suite 538 passed. The ERROR backlog is the next topic.
