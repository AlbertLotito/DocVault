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

## 38. Known Issues (supersedes §36 of the 2026-08-01 doc)

- **WMI CPU temp sensor** — still fails on some machines with COM error 0x80041003. Falls back to dummy (0°C). Non-critical.
- **SQLite lock contention** — full fix (write serialisation) still deferred.
- **`Moved:` log noise** — cosmetic false-positive for hash-duplicate boilerplate content (see 2026-08-01 doc §35). Not yet fixed at the source.
- **Pre-existing test failures** — 2 collection errors + 6 failing tests, unrelated to any recent feature (see §37 Test Status above). Not yet triaged.
