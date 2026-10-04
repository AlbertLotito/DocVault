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

---

## 52. ERROR Backlog: UNKNOWN Status Fixed, Email Extractor, Audacity Cleanup (2026-10-03, master, 470ff68 → b5b1526)

68,680 tasks (35%) were ERROR. Analysis showed 63,767 (93%) were **files with no extractor** (1,276 types), reported as ERROR by the fallback kernel. Spec: `docs/superpowers/specs/2026-10-03-unknown-status-and-email-design.md`.

**UNKNOWN status (`6442ac3`).**
- **Cause:** the worker was always meant to mark unroutable types `UNKNOWN`, and the UI is wired for it: Vault filter and tile, the unknown-types list, telemetry. But it compared the router's result with the `fallback_kernel` *module*, while the router returns a `LazyPythonKernel` proxy, so the check never matched. The unit test mocked the router with the module, so it passed.
- **Fix:** compare with `router.FALLBACK_KERNEL`.
- **New behaviour:** `router.reload()` re-queues UNKNOWN tasks whose type gained a kernel (`manager.requeue_unknown`), so activating a kernel in the Lab picks up its files automatically. A conftest fixture stubs that hook so tests never touch the real DB.
- **Migration:** `init_db` converts existing fallback ERRORs to UNKNOWN.
- **Result: ERROR 68,680 → 1,551.**

**Email extractor (`b5b1526`).**
- **The gap:** 23,124 Thunderbird emails (`outlookdata\ThunderbirdRoot\*.wdseml`, Windows Search's plain RFC-822 copies) were unsearchable. This is the "outlookdata 22,095 ERROR" from §48.
- **New `email_extractor`** (`.eml`, `.wdseml`; stdlib `email`):
  - From/To/Cc/Subject/Date header block plus metadata
  - plain-text body, or the HTML body rendered by the HTML kernel
  - attachment names listed, contents not extracted
- **Real-file check:** 498/500 real messages parse at 17 ms each; the 2 rejects are encrypted/compressed blobs.
- **Rollout proved the re-queue path:** after the restart, the router reload moved all 23,124 back to PENDING with no manual step. They process at about 5/s end to end. Search by subject verified through the MCP server.

**Audacity.** All 3,367 `.au` files were Audacity project block files (`*_data\e00\d01\*.au`), which ffmpeg can't load on their own. An `au` extension ignore rule was added on the Documents vault, and the rows purged. A `*_data` folder rule was rejected: it would also have hidden 62 unrelated `test_data`/`sample_data` files.

**Deliberately left UNKNOWN:** about 14k third-party code/config files (`.h`, `.pyi`, `.pm`, `.pl`, `.tcl`, `.ini`, …), binaries, and about 15k extension-less files (89% binary program data). To index a type later, activate a kernel for it; its files re-queue automatically.

**Remaining real ERRORs (1,551), next round:**
- Google Drive `.gdoc` API errors: 408
- PDF images needing `jbig2dec`: 60, plus other image failures: about 120
- Legacy `.doc` via Word automation: 85
- `.xls` (openpyxl can't read it, needs xlrd): 74
- Archives: 56
- HTML pages with no text: 37
- Image-only EPUBs: 21
- ffmpeg failures on other media: about 600 (`.mp3` etc., not yet triaged)
- Corrupt files: 16 "database disk image is malformed", 1 too big

Full suite: 556 passed.

---

## 53. ERROR Round 2: .xls, Standalone .gz, Media (2026-10-04, master, 9d1d6a6 → 2a98fb0)

The ERROR breakdown by actual cause (§52 left 1,551 real failures).

**Legacy `.xls` (74), `9d1d6a6`.**
- **Cause:** the Excel kernel claimed `.xls` but only used openpyxl, which reads `.xlsx` only.
- **Fix:** `.xls` now goes through `xlrd` with the same `[Sheet]` + TSV output (real dates, integers without `.0`, xlrd's stdout warnings silenced). Files named `.xls` that aren't workbooks (TSV/CSV exports, Excel "Save as Web Page" HTML, often UTF-16) fall back to text/HTML rendering.
- **Result:** 72/74 extract; 2 are empty workbooks. `xlrd>=2.0` added; small `.xls` fixture committed (generated with xlwt, which was not kept).

**Standalone `.gz`/`.bz2` (56), `64caabe` + `e179cf7`.**
- **Cause:** every `.gz` was treated as a tar archive.
- **Fix:** a single compressed file is now decompressed. Text is indexed, capped at 5 MB with truncation noted; a binary payload is described instead of erroring.
- **Result:** 52/56 extract (mostly rotated web-server access logs); 4 are genuinely corrupt zip/7z files.
- **Process slip:** `64caabe` was committed while a legacy test still asserted the old error result. It was fixed in `e179cf7`, and later commits are gated on a green suite.

**Media (596), `2a98fb0`.**
- **Diagnosis:** the stored errors held only ffmpeg's version banner; the real error, printed last, was cut off. Re-running ffmpeg on them showed **they decode fine**: 586 of the 596 failed within one window, 01:24–01:34 on 2026-07-16, during the drive-migration storage instability. They were re-queued for Whisper transcription; this takes GPU hours, which the user approved, and the throttle protects other GPU work.
- **Fix:** `core/extractors/errors.summarize_ffmpeg_error()` strips the banner and keeps the last real lines. It's used by the aural, media-diagnostics and video kernels (v1.0.1).
- **First new failure** read "Failed to find two consecutive MPEG audio frames… Invalid data found", a dummy `Test.mp3` in an ID3-tool folder.

**Side finding:** 9 WhatsApp `.opus` voice notes are now correctly UNKNOWN; no kernel claims `.opus`. Whisper decodes Opus, so adding `opus` to the aural kernel's extensions would pick them up automatically through the UNKNOWN re-queue.

All 5 changed kernels were re-certified with the server stopped; 731 files reset to PENDING. Full suite 576 passed.

**Remaining ERROR** (about 845 plus whatever the reprocessing turns up):
- Google Drive `.gdoc` API errors: 408
- Legacy `.doc` via Word automation: 85
- `jbig2dec` and other image failures: about 180
- Image-only EPUB / HTML / PPT pages: about 70
- Corrupt files: about 25

---

## 54. Scanned-PDF OCR Restored; .opus Voice Notes (2026-10-04, master, bcabbdf → 1085129)

**`.opus` (`bcabbdf`).** Added to the aural and media-diagnostics kernels (v1.0.2), mirroring `.mp3`. The swap was done live: workers paused, both kernels decertified and re-certified through the Lab endpoints, workers resumed. The router reload re-queued the 9 UNKNOWN WhatsApp notes automatically.

**Scanned-PDF OCR was silently broken (`1085129`).** Investigating the 60 "jbig2dec binary is not available" PDFs showed they had no text at all because OCR never ran.
1. **`pdf2image` was missing** from the venv and had never been in `requirements.txt`, probably lost in a venv rebuild. Page rendering failed, so OCR was skipped. Installed and added to requirements.
2. **`core.logger` only guarded `OSError`.** A debug line containing `→` raised `UnicodeEncodeError` on a cp1252 console inside the extractor, so OCR failed even with #1 fixed. That also explains the 4 "'charmap' codec" ERRORs. The logger now prints with replacements and never raises.
3. **Garbage text layers** (`\x00\n\x01…`) counted as text and were never OCR'd. Control characters are now stripped, and sparseness counts letters/digits in any script.

Also in `text_extractor` v1.1.0:
- Only the sparse pages are rendered, one at a time. Whole-document rendering at 300 DPI risked GBs of RAM on long scans.
- A relative `pdf:poppler_path` now resolves against the project root, not the cwd.

**Known limit:** glyph-index gibberish text layers (`0012345676874…`) still pass as text.

**Reprocess:** 1,674 tasks reset: 184 PDF ERRORs, 1,486 "completed" PDFs with under 100 chars of text (stale vectors and FTS rows removed first), and 4 charmap errors.
- **Scale:** 98,654 pages, and 312 scanned books hold 89,348 of them. Roughly 80–250 h of OCR.
- **Ordering:** the 311 PDFs of 100+ pages were set to `priority=1`, so short documents and the audio queue go first; the books grind on in the background through the age bonus.

**`jbig2dec` decision:** not installed. With OCR working, those PDFs get their text; `jbig2dec` would only let the image harvester extract the embedded JBIG2 page images for vision descriptions, which mostly duplicate the OCR. Revisit if wanted.

---

## 55. PDF Text-Layer Measurement, Gibberish Detection, Full PDF Re-Extraction (2026-10-04, master, 5b76c7d)

**Measurement** (read-only, scratchpad script): 300 random text-layer-only COMPLETED PDFs (population 5,009) plus 40 of 160 flow sheets.
- **Missed OCR, the big finding:** 70% have at least one image-only page that was never OCR'd (the `pdf2image` regression, §54). 51% have *no* real text on any page; they looked "completed" only because the image harvester attached AI page descriptions, often invented ("[SOCIAL NARRATIVE] The individuals depicted…" for sheet music and RN notes). Extrapolated: about 3,500 PDFs and about 58k pages.
- **Glyph gibberish:** about 150 of 160 flow sheets, plus 3–5% of other PDFs: `/0 /1 /2` glyph references (PA court/DMV forms) and glyph-index strings.
- A first, page-1-only version of the check wrongly called empty-cover PDFs "garbage"; it was caught on inspection and replaced.

**`5b76c7d` (text_extractor v1.2.0):**
- glyph-reference layers are treated as empty, so they get OCR'd
- number-heavy, wordless layers get compare-and-pick: OCR wins only if it reads at least 10 real words and the layer contains less than 25% of them
- **verified on real files:** the flow sheet, court and DMV forms now read correctly; the TSR book's usable layer and the bilingual instructions' real text are kept

**Re-extraction:** all 5,009 text-layer-only PDFs were re-queued, with their vectors and FTS rows removed first. The 559 of 100+ pages were set to priority 1. **Trade-off accepted:** the old AI page descriptions attached to scans are not regenerated.
- **Queue after restart:** about 7,458 PENDING (6,639 PDFs, about 565 audio, images). That's several days of background OCR, throttled.

**Side finding, not fixed:** the face/scene "social narrative" kernel runs on images with no people (sheet music, typed pages) and invents scenes.

---

## 56. Image Narratives, PDF-Image MISSING Bug, UI Testing Round (2026-10-04, master, c3ac335 → b397766)

**Invented "social narratives" (`c3ac335`).**
- **Cause:** the face-narrative kernel's prompt presupposes people ("Analyze the people in this image…") and it ran on every image, so 25,154 stored texts described invented scenes (sheet music, typed nursing notes).
- **Fix:**
  - it now runs only when `core/face_detect.py` (the identity kernel's Haar settings) finds a face
  - "no faces" is an empty result, not an error, in all three face kernels
- **General image kernel:** `intelligent_image_extractor` was re-enabled; it had been decertified after 08-30 like the plaintext one. PDF page images get Tesseract first and the vision model only if OCR finds nothing (likely an embedded photo). An OCR-only, face-less page image doesn't write back into its parent PDF (that would duplicate the parent's OCR).

**PDF images flagged MISSING (`997641b`).**
- **Cause:** the init_db `file_vault` backfill gave child tasks (images the PDF harvester cuts into `.cache\extracted_images`) vault rows with `.cache` paths. Vault scans never walk `.cache`, so the deleted-file detector flagged all of them. **39,690 of 39,828 MISSING were these; 138 were real.**
- **Decision (user):** children are reachable only through their parent PDF.
- **Fix:**
  - they get no `file_vault` rows; init_db repairs existing ones and restores their status
  - missing detection ignores them
  - no FTS rows and no embedding (the worker completes them directly)
  - filename search excludes them
  - re-dispatching a child (when the parent is re-extracted) re-queues it so it writes back into the fresh parent text
- **Deployed:** 39,975 children's standalone FTS rows and vectors removed, and 39,225 standalone images re-queued for the fixed kernels. MISSING went from 39,828 to 138, and the user then purged those.

**UI testing round** (user, step by step: Browse, tray popup, Vault, Search). Fixes for what it found:
- `ebc91bd`: right-click menus opened off-screen after scrolling. They were `position:fixed` but placed with `pageX/pageY`; the shared `lcOpenCtxMenu()` uses `clientX/Y` and keeps the menu in the window (Browse, Vault).
- `47ce150`: the unknown-file-type chips used `--c-unk` (#666) for text; now `--c-label`.
- `9af83b9`: **Purge returned 500.** `tasks.parent_hash` references `tasks(file_hash)` without cascade, so purging a MISSING PDF with harvested images raised "FOREIGN KEY constraint failed" and rolled back. `purge_file_hashes()` now purges the children too, deepest first. This was reproduced against the real schema in a scratch DB.
- `b397766`: `/api/stats` lacked a `missing` count; the parts now add up to the total.

The tray popup's deferred "mouse wheel" and "long paths" items were tested and work, so they're dropped.

**Housekeeping:** the 62 GB LanceDB backup at `D:\DocVault-backups` was approved for deletion. Claude Code's safety check blocks removing a drive-root folder, so the user deletes it.

**Still open:**
- Google Drive `.gdoc`: 407 × 404. The stubs belong to `saltheart.foamfollower@` (277) and `albert.g.lotito@` (130), but DocVault has one OAuth token, account unknown.
- Legacy `.doc` via Word COM: 85.
- Dudeskie on hold (in development).
