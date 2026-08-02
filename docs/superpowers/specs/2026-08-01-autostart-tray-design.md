# DocVault Autostart + System Tray — Design Spec
**Date:** 2026-08-01
**Status:** Approved

---

## 1. Problem

DocVault must currently be started manually every session by running `start.ps1` in a visible terminal window, which has to stay open for the app to keep running. This is inconvenient day-to-day and doesn't survive a reboot without the user noticing and relaunching it by hand.

## 2. Goals

- DocVault (and Ollama, via `start.ps1`'s existing logic) starts automatically when the user logs into Windows, with no visible console window.
- A system tray icon provides a lightweight, always-available shortcut to the DocVault UI, without needing to remember the URL or keep a terminal open.
- Preserve all of `start.ps1`'s existing logic untouched — Ollama port-conflict auto-recovery, model pulling, and the crash-restart loop with exponential backoff.
- Keep the tray helper fully decoupled from the server process — it never starts, stops, or owns the server; it is purely a shortcut to it.

## 3. Non-Goals

- No process supervision/ownership by the tray icon. That responsibility stays entirely with Task Scheduler + `start.ps1`'s own restart loop.
- No pause/resume, restart, or stop controls in the tray menu — those already exist in the web UI (Search Mode toggle, Mission Control) and are out of scope for v1.
- No live status reflection in the tray icon (color changes, badges, polling `/api/monitor/status` or `/utils/health`) — a static icon only for v1.
- No crash-recovery for the tray helper process itself — if it fails to launch, the (fully independent) DocVault server task is unaffected.

## 4. Architecture

Two independent Windows Task Scheduler tasks, both triggered **"At log on"** for the current user (explicitly not "run whether logged on or not"), so everything runs inside the normal interactive desktop session — this matters because Ollama's GPU access is more reliable there than under a service account in Session 0.

| Task | Action |
|---|---|
| `DocVault-Server` | `powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File "D:\DocVault\start.ps1"` |
| `DocVault-Tray` | `D:\DocVault\venv\Scripts\pythonw.exe "D:\DocVault\tray\tray_app.py"` |

`pythonw.exe` (the windowless variant of the interpreter already in the venv) means the tray helper never creates a console at all — no `-WindowStyle` trick needed for it specifically. `start.ps1` still gets `-WindowStyle Hidden` explicitly since it's a PowerShell host process running in the interactive session and would otherwise show a console.

## 5. Tray Helper (`tray/tray_app.py`)

New, intentionally minimal script using `pystray` (+ `Pillow`, already a dependency):

- Loads `frontend/static/tray_icon.ico` (already created — the magnifying-glass detective illustration, converted to a multi-resolution `.ico` with a transparent background).
- Reads `[server] host` / `port` from `config.ini` at startup to build the target URL — no hardcoding, matching the project's existing convention of reading configurable values rather than hardcoding them.
- Menu: **"Open DocVault"** → `webbrowser.open(url)`; **"Exit"** → `icon.stop()` (removes only the tray icon; never touches the server).
- Double-click maps to the same "Open DocVault" action (pystray's default-item mechanism).
- No polling loop, no subprocess management, no health checks — just an icon plus two actions.

## 6. `register_autostart.ps1` (new)

A one-time, but safely re-runnable, setup script:

- Creates both scheduled tasks above via `Register-ScheduledTask`, trigger `At log on` for the current user.
- Idempotent: checks for existing tasks by name (`DocVault-Server`, `DocVault-Tray`) and updates the action path if already registered, rather than erroring or duplicating. This matters directly because of the drive-letter migration this project just went through — re-running this script is how a future move gets autostart repointed at the new location, instead of leaving a stale path silently broken inside Task Scheduler's GUI/XML state.
- Prints a summary of what was created vs. updated.

## 7. `requirements.txt`

Add `pystray`. `Pillow` is already present (used for the icon conversion and by other extractors).

## 8. Testing / Verification

Manual verification (no automated test suite meaningfully covers a Task-Scheduler-triggered GUI feature):

1. Run `register_autostart.ps1`; confirm both tasks appear in Task Scheduler with the correct triggers/actions.
2. Log off and back on (or reboot): confirm no visible console window appears at any point.
3. Confirm DocVault becomes reachable at its configured URL once Ollama + server finish their normal startup sequence (same timing as a manual `start.ps1` run today).
4. Confirm the tray icon appears (check the hidden-icons overflow area — new tray icons often land there by default on Windows).
5. Right-click: confirm both menu items are present; "Open DocVault" opens the browser to the right URL; double-click does the same.
6. Click "Exit": confirm only the tray icon disappears, and the server is still reachable in the browser afterward (proving it was never touched).

## 9. Rollout

Purely additive — a new `tray/` directory, the `.ico` asset (already in place), one new setup script, two new scheduled tasks, and one new dependency. No migration needed, and anyone who prefers running `start.ps1` by hand can keep doing so unaffected.
