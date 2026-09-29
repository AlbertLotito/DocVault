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

## 48. Untracked Files Found in the Working Tree (2026-09-29, under review)

Three files have been untracked since late August / mid-September and aren't in any commit or earlier status doc:

| File | State |
|---|---|
| `extractors/epub_extractor.py` | **Live in production.** Certified in `ext_registry` 2026-08-30 as `com.docvault.document.epub`. It has processed 2,237 EPUBs: 2,215 COMPLETED and 22 ERROR. Of the errors, 21 are "No text content" (probably image-only EPUBs) and 1 is an `ebooklib` parse failure. Dependencies (`ebooklib`, `lxml`) are installed. A fresh checkout wouldn't have it. |
| `extractors/svg_extractor.py` | **Live in production.** Certified 2026-08-30 as `com.docvault.vision.svg`. It rasterizes SVGs with `svglib`/`reportlab`, then runs the vision model with a Tesseract fallback. 55 SVGs are COMPLETED. |
| `frontend/browse.html` | **Frontend only; the backend doesn't exist.** It calls `GET /api/catalog/tree` and `POST /api/catalog/{hash}/scan_now`, and the page needs a `/browse` route in `api/main.py` plus a nav entry. None of these exist in git history, stashes or worktrees; the live server returns 404 for `/browse` and `/api/catalog/tree`. **`README.md` and `QUICKSTART.md` (c09244d) already advertise this page and use it as the post-install smoke test**, so the docs currently describe a feature that doesn't work. |

Decision pending with the user.

---

## 49. Known Issues

- **Browse page docs describe a feature that doesn't work.** See §48.
- **Deferred tray-popup polish.** The minor, non-blocking items from §38 are still open.
- **Unexplained clean exit on 2026-09-21.** See §46; parked.
- WMI CPU temp and SQLite lock contention are still closed as non-issues (see the 2026-08-02 doc §39).
