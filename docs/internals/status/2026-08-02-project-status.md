# Project Status — 2026-08-02

Supersedes all earlier status documents.

---

## 1–36. Previous Work — COMPLETE

See `docs/internals/status/2026-08-01-project-status.md` for full details on all prior work
(Span Grounding, Qdrant Removal, Art Enrichment Reliability, Claude API Key & Model as Settings,
Deleted-File Detection, the E:→D: drive migration, and the `docvault.db` corruption recovery).

---

## 37. Windows Autostart + System Tray — COMPLETE (2026-08-01–02, master, c3dd49d → bed5f34)

DocVault previously had to be started manually every session via `start.ps1` in a visible terminal window that had to stay open. Built via the full brainstorming → spec → plan → subagent-driven-development cycle, on master directly (7 commits, matching this project's established workflow for the last three features).

### What Was Built

| Component | Description |
|---|---|
| `tray/tray_app.py` | Minimal `pystray`-based tray helper. `get_server_url()` reads `[server] host`/`port` from `config.ini` at runtime (never hardcoded). Menu: "Open DocVault" (`webbrowser.open`), "Exit" (`icon.stop()` only). `main()` wraps startup in try/except, logging any failure to `tray_app_error.log` next to the script — added after final review flagged that a bare exception under `pythonw.exe` (no console) would otherwise vanish with zero trace. |
| `frontend/static/tray_icon.ico` | User-supplied illustration (magnifying-glass detective motif), converted to a multi-resolution `.ico` with real alpha transparency. |
| `register_autostart.ps1` | Idempotent: creates or updates two Scheduled Tasks — `DocVault-Server` (runs existing `start.ps1` hidden) and `DocVault-Tray` (runs the tray script via `pythonw.exe`) — both triggered `At log on`, explicitly scoped to the current user via `-User` on the trigger itself (not just the principal). |
| Architecture | Tray is a **companion, never a supervisor** — it does not start/stop/own the DocVault server process (user's explicit design choice during brainstorming). This costs nothing because `/utils/shutdown` already makes `run.py` exit 0, which `start.ps1`'s restart loop already treats as "stop, don't relaunch." |

### Bugs Found and Fixed During Review

1. **False success reporting**: `register_autostart.ps1`'s original code (verbatim from the plan) printed `[OK] Created task` even when `Register-ScheduledTask` failed with Access Denied, because `ScheduledTasks` module cmdlets don't honor a script-level `$ErrorActionPreference = 'Stop'` — each call needs its own explicit `-ErrorAction Stop` to become catchable. Fixed: each cmdlet wrapped in its own `try/catch`, both tasks always attempted regardless of the first's outcome, script exits 1 if either failed.
2. **Untracked required asset**: `frontend/static/tray_icon.ico` was built and verified earlier in the session (before the plan's task list existed) but never `git add`ed. It rode along uncommitted through all 4 SDD tasks and their reviews undetected, since none of the 4 tasks' commit steps happened to reference it — only the final whole-branch review caught it. A fresh checkout would have shipped code that calls `Image.open()` on a file that doesn't exist, failing silently under `pythonw.exe`.

### End-to-End Verification — CONFIRMED (2026-08-02, user, elevated PowerShell)

`Register-ScheduledTask` requires elevation, which no Claude Code session in this environment could obtain (UAC split-token, confirmed via three independent reproduction attempts). User ran the full verification manually from an elevated PowerShell: `register_autostart.ps1` created both tasks, a second run updated them in place (idempotency confirmed), `Start-ScheduledTask` brought up both with no visible console and a working tray icon, and a real logoff/logon cycle confirmed true autostart. No gaps remain.

### Test Status

The feature's own 6 tests (`tests/test_tray_app.py`) pass cleanly. A full-suite run surfaced 8 pre-existing failures/errors unrelated to this feature (confirmed via `git diff --stat` — this feature's commits touch only `tray/`, `tests/test_tray_app.py`, `register_autostart.ps1`, `requirements.txt`, and the icon): `test_router.py` and `test_video_extractor.py` fail to collect (stale imports — `UNKNOWN` missing from `core.router`, `video_extractor` missing from `extractors`), plus 6 failing tests across `test_embed_throughput_settings.py`, `test_embedding_worker.py` (×3), `test_i18n.py`, and `test_search_throttle.py`. Not fixed as part of this feature (out of scope); flagged here for whoever picks them up next.

---

## 38. Tray Search Popup — COMPLETE (2026-08-02, master, db13076 → 81ab2ca)

Adds a **"Search..."** item to the tray menu (built in §37) that opens a floating, non-modal popup — text entry, a search-type dropdown (Hybrid / Full-text / Semantic / Filename), and a results pane — backed by the already-running DocVault server's search API. Built via the full brainstorming → spec → plan → subagent-driven-development cycle (6 tasks + final whole-branch review), on master directly, 12 commits.

### What Was Built

| Component | Description |
|---|---|
| `create_popup(x, y)` | Builds a `tk.Toplevel` per invocation — every "Search..." click spawns an independent popup (no single-instance enforcement, by design). Entry + `ttk.Combobox` + Search button + status label + scrollable results canvas. Non-modal (no `grab_set()`), `-topmost` set once at creation. |
| One hidden `tk.Tk()` root | Created once on a dedicated background thread (`start_popup_host`), owns every popup as a `Toplevel` under a single Tcl interpreter — avoids the undocumented/flaky behavior of multiple simultaneous `Tk()` instances across threads. `pystray`'s own icon loop is untouched on the main thread. |
| `_popup_queue` (thread-safe) | The only channel between pystray's callback thread, HTTP worker threads, and the Tk thread. `_drain_queue()` polls it every 100ms via `root.after()`; `handle_message()` dispatches `spawn`/`results`/`open_result_done` messages to the right widget-touching function. No Tk widget is ever touched off the Tk thread. |
| `perform_search()` / `open_result()` | HTTP calls (via `httpx`, already a dependency) to the server's existing `GET /api/search`, `GET /api/search/filename`, and `POST /api/utils/open_path` — the last one reused rather than duplicating its vault-root + blocked-extension validation locally. Double-click on a result opens the file. |
| Pure decision helpers | `format_result_summary`, `extract_search_request`, `rows_for_response`, `status_for_open_result` — all Tk- and network-free, fully unit tested; this is what let a "GUI feature" end up with 45 tests instead of none. |

### Bugs Found and Fixed During Review

1. **Missing `/api` prefix (the big one)**: `build_search_request()` and `open_result()` originally built URLs without the `/api` prefix that `api/main.py` actually mounts the `search`/`utils` routers under — and `api/main.py` separately has an *unprefixed* `GET /search` that serves the HTML search page. Net effect: every search silently got back a webpage instead of JSON, and file-opening 404'd — **the entire feature was non-functional against the real server**, and this passed five task reviews undetected because every review checked the code against the plan, and the plan itself had the bug. Only the Task 6 implementer's manual end-to-end test against a live server caught it. Fixed in the same task's diff; a regression test (`test_tray_urls_match_server_routes`) now checks the tray's constructed URLs against the server's actual `app.routes` table.
2. **Popup spawns almost entirely offscreen**: cursor position at tray-click time is always near a screen corner (that's where the tray is); the original `geometry()` call didn't clamp, so ~95% of the window rendered off the desktop. Fixed with `clamp_popup_position()` keeping the full window on-screen.
3. **`_drain_queue` died permanently and silently on any exception**: only `queue.Empty` was caught; any other exception (a malformed message, a `TclError`) killed the `root.after()` reschedule, silencing the entire popup subsystem for the rest of the process with zero diagnostics (worse under `pythonw.exe`, no console). Fixed: broad per-message exception catch logging to `tray_app_error.log`, reschedule moved to `finally`.
4. **GUI test suite flaking 8-of-9 full runs**, not the ~1-in-3 first assumed — root cause was each test creating and destroying its own fresh `tk.Tk()` root (production creates exactly one, ever). Fixed with a `scope='session'` pytest fixture shared across all GUI tests; verified clean across 10 consecutive full-suite runs.

### Deferred (Minor, non-blocking)

Path not truncated in the results pane (spec said truncated); no mousewheel binding on the results canvas; no guard against a slow search's stale response landing after a faster later one; non-200 status always shows the generic "Could not reach DocVault server." message even for a 4xx/5xx that isn't a reachability problem; `degraded_reason` gets dropped when results are also empty; a small `_popups.get(popup_id)` guard is duplicated across three functions; `handle_message` has no `else` for an unrecognized message kind; `base_url` is frozen at popup-creation time rather than re-read per search; no explicit quit message flushes Tk state on tray Exit while popups are open; `httpx` trusts proxy env vars by default. None block merge; picked up here for whoever touches this file next.

### Test Status

45/45 tests in `tests/test_tray_app.py` pass, confirmed clean across 10 consecutive runs (after fixing the Tk-root test flakiness above). Full-suite run shows the same 8 pre-existing failures/errors already documented in §37 — unrelated to this feature, not touched by it.

---

## 39. Known Issues (supersedes §38 of the earlier revision of this doc)

- **WMI CPU temp sensor** — no longer tracked as an open issue (2026-08-02): confirmed the existing fallback (`core/monitor.py` `BaseSensor.initialise()`/`read()`, lines 84-99) already degrades cleanly — any `_init_hardware()` failure is caught once, the sensor is marked unavailable, and reads return an all-zero dummy forever after with no repeated errors or spam. The only effect is CPU-temp-based thermal throttling never fires on affected machines; GPU thermal throttle, RAM, and disk-pressure protections are independent and unaffected. The `0x80041003` (`WBEM_E_ACCESS_DENIED`) error itself points to OEM/vendor firmware locking down `MSAcpi_ThermalZoneTemperature` under `root/wmi` on those specific machines, not an app bug. A real fix (elevation, or an external provider like LibreHardwareMonitor's WMI namespace) was deliberately not pursued — disproportionate effort for a non-critical, already-gracefully-degraded signal. Revisit only if real CPU temps are wanted on a specific machine that already runs a compatible hardware-monitoring tool.
- **SQLite lock contention** — no longer tracked as an open issue (2026-08-02): `core/manager.py`'s existing mitigations (WAL mode, `synchronous=NORMAL`, 30s busy timeout, short-lived per-call connections — `_connect()` lines 185-196) are handling real usage with no reported symptoms. An app-level write-serialization queue was considered and deliberately not built — it would solve a problem not currently occurring on this single-user desktop app. Revisit only if `database is locked` errors actually reappear.
- **`Moved:` log noise** — fixed at the source 2026-08-02, see §41 below.
- **Pre-existing test failures** — down to 6 failing tests (2 collection errors resolved 2026-08-02, see §40 below), unrelated to any recent feature. Not yet triaged: `test_embed_throughput_settings.py`, `test_embedding_worker.py` (×3), `test_i18n.py`, `test_search_throttle.py`.

---

## 40. Stale Router/Video-Extractor Tests Fixed + Latent Router Bug Found (2026-08-02, master, 27277bb)

`tests/test_router.py` and `tests/test_video_extractor.py` had been failing to collect since the Kernel Registry refactor (`031c28d`, `91816c6`) — both tested a pre-refactor architecture that no longer exists (`test_router.py` imported a removed `UNKNOWN` constant against the old static `ROUTES` dict; `test_video_extractor.py` imported the renamed/rewritten `video_extractor` module, now `multimodal_video_intelligence_extractor` with a changed `extract()` signature). Rewrote both against the current dynamic, DB-backed kernel router (seeding `ext_registry` via the same `settings_db` fixture pattern as `test_registry_certify.py`, then calling `router.reload(sync_disk=False)`) and the current extractor (mocking `aural_intelligence_extractor.extract` and `vision_enabled` instead of the old standalone `transcriber` module).

**Real bug found while writing the new router tests, not test-only**: `core/router.py`'s `reload()` never reset the module-level `FALLBACK_KERNEL` global before rebuilding routes — it only ever *set* it when a `'*'`-extension kernel was found, so once any reload encountered a fallback kernel, that fallback stayed stuck in memory forever, even after a later reload's active-kernel set no longer included one. In production this meant deactivating a fallback kernel via the Extractor Lab's hot-reload would silently keep routing unmatched file types to the old, deactivated kernel instead of correctly returning no match. Fixed by rebuilding `FALLBACK_KERNEL` fresh on every `reload()` call, matching how `ROUTES`/`_all_extractors` already work; regression test added (`test_reload_clears_stale_fallback_kernel`).

Full suite confirmed clean across 2 repeated runs: 409 passed, only the 6 pre-existing unrelated failures remain (see §39).

---

## 41. `Moved:` Log Noise Fixed at the Source (2026-08-02, master, d552e35)

Previously confirmed cosmetic-not-destructive (2026-07-26–08-01 drive migration session, see 2026-08-01 doc §35 and [[common-bugs]]) but never fixed at the source. Root cause, confirmed by reading `core/ingestor.py`: when two different files within the same vault share the same content hash (common for byte-identical Office boilerplate parts — `clip_colorschememapping.xml` etc. — across many old `.doc`/`.docx` files), the ingestor unconditionally treated the second one encountered in a scan as the first having "moved" to it, flipping the registered canonical path (`file_vault` + `tasks.file_path` + vector store path) on every single scan regardless of `os.walk()` ordering — which isn't guaranteed stable run to run. Reproduced deterministically in a test with two hash-identical files coexisting in one vault (`tests/test_ingestor.py::test_duplicate_content_files_are_not_flagged_as_moved`) — it triggers on the very first scan a vault sees such files, not just re-scans.

**Fix**: a same-hash path mismatch is only treated as a real move when the old registered path no longer exists on disk. If it still exists, it's a content duplicate, not a move — leave the canonical path untouched (`core/ingestor.py`, the `elif os.path.normpath(vault_path) != file_path` branch now checks `os.path.exists(vault_path)` first). Genuine renames (old path actually gone) are still detected and logged exactly as before — covered by a second regression test, `test_genuinely_renamed_file_is_still_flagged_as_moved`.

Full suite confirmed clean: same 6 pre-existing unrelated failures only (see §39).
