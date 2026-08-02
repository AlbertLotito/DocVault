# DocVault Autostart + System Tray Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DocVault (and Ollama) start automatically at Windows logon with no visible console window, and add a system tray icon that opens the DocVault UI without needing a terminal.

**Architecture:** Two independent Windows Scheduled Tasks, both triggered "At log on" for the current user. One runs the existing `start.ps1` hidden (untouched — Ollama launch, port-recovery, and crash-restart-loop logic all stay as-is). The other runs a new, minimal Python tray helper (`tray/tray_app.py`, via `pystray`) that never starts, stops, or supervises the server — it is purely a shortcut. A new `register_autostart.ps1` script creates/updates both tasks idempotently.

**Tech Stack:** Python 3.13 (existing venv), `pystray` (new dependency), `Pillow` (already present), PowerShell 5.1+ `ScheduledTasks` module, pytest.

## Global Constraints

- Tray helper never starts/stops/supervises the DocVault server process — it only opens a browser to it (spec §3, §5).
- Both scheduled tasks trigger "At log on" for the current user, never "run whether logged on or not" — keeps everything in the interactive session for reliable Ollama GPU access (spec §4).
- `start.ps1`'s existing Ollama/crash-restart logic is not modified in any way.
- No live status polling or icon-state changes in the tray helper for this feature (spec §3).
- No crash-recovery for the tray helper process itself (spec §3).
- Tray helper reads the server URL from `config.ini`'s `[server]` section at runtime — never hardcode `localhost:8050` (spec §5).
- `register_autostart.ps1` must be safely re-runnable: update existing tasks by name rather than erroring or duplicating (spec §6).

---

### Task 1: Tray package scaffold + config-driven URL builder

**Files:**
- Create: `tray/__init__.py` (empty — matches existing package convention, see `core/__init__.py`, `llm/__init__.py`)
- Create: `tray/tray_app.py`
- Modify: `requirements.txt` (add `pystray`)
- Test: `tests/test_tray_app.py`

**Interfaces:**
- Produces: `get_server_url(config_path=CONFIG_PATH) -> str` — reads `[server] host`/`port` from an INI file and returns `http://{host}:{port}`, falling back to `127.0.0.1:8050` if the section/keys are absent.
- Produces module constants: `PROJECT_ROOT`, `CONFIG_PATH` (both later tasks read `PROJECT_ROOT` to build `ICON_PATH`).

- [ ] **Step 1: Add the dependency**

Append a new line to `requirements.txt`:
```
pystray
```

- [ ] **Step 2: Create the package init**

Create `tray/__init__.py` with empty contents (0 bytes).

- [ ] **Step 3: Write the failing tests**

Create `tests/test_tray_app.py`:
```python
import os

from tray.tray_app import get_server_url


def _write_config(tmp_path, contents):
    config_path = os.path.join(str(tmp_path), 'config.ini')
    with open(config_path, 'w') as f:
        f.write(contents)
    return config_path


def test_get_server_url_reads_config(tmp_path):
    config_path = _write_config(tmp_path, '[server]\nhost = 127.0.0.1\nport = 8050\n')
    assert get_server_url(config_path) == 'http://127.0.0.1:8050'


def test_get_server_url_defaults_when_section_missing(tmp_path):
    config_path = _write_config(tmp_path, '[other]\nkey = value\n')
    assert get_server_url(config_path) == 'http://127.0.0.1:8050'


def test_get_server_url_uses_custom_host_and_port(tmp_path):
    config_path = _write_config(tmp_path, '[server]\nhost = 0.0.0.0\nport = 9999\n')
    assert get_server_url(config_path) == 'http://0.0.0.0:9999'
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `D:\DocVault\venv\Scripts\python.exe -m pytest tests/test_tray_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tray.tray_app'` (file doesn't exist yet)

- [ ] **Step 5: Implement `tray/tray_app.py`**

```python
"""DocVault system tray helper."""
import configparser
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.ini')


def get_server_url(config_path=CONFIG_PATH):
    """Read [server] host/port from config.ini and build the base URL."""
    parser = configparser.ConfigParser()
    parser.read(config_path)
    host = parser.get('server', 'host', fallback='127.0.0.1')
    port = parser.get('server', 'port', fallback='8050')
    return f'http://{host}:{port}'
```

- [ ] **Step 6: Install the new dependency and run tests to verify they pass**

Run: `D:\DocVault\venv\Scripts\python.exe -m pip install pystray`
Run: `D:\DocVault\venv\Scripts\python.exe -m pytest tests/test_tray_app.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add tray/__init__.py tray/tray_app.py tests/test_tray_app.py requirements.txt
git commit -m "feat: add tray helper package with config-driven server URL builder"
```

---

### Task 2: Menu construction and click callbacks

**Files:**
- Modify: `tray/tray_app.py`
- Modify: `tests/test_tray_app.py`

**Interfaces:**
- Consumes: `get_server_url()` from Task 1.
- Produces: `open_docvault(icon=None, item=None)`, `exit_tray(icon, item)`, `build_menu() -> pystray.Menu` (later Task 3 calls `build_menu()` to construct the running icon).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tray_app.py`:
```python
from unittest.mock import MagicMock, patch

from tray.tray_app import build_menu, exit_tray, open_docvault


def test_build_menu_has_open_and_exit_items():
    menu = build_menu()
    items = list(menu)
    assert len(items) == 2
    assert items[0].text == 'Open DocVault'
    assert items[0].default is True
    assert items[1].text == 'Exit'


@patch('tray.tray_app.webbrowser.open')
@patch('tray.tray_app.get_server_url', return_value='http://127.0.0.1:8050')
def test_open_docvault_opens_browser_to_server_url(mock_get_url, mock_open):
    open_docvault()
    mock_open.assert_called_once_with('http://127.0.0.1:8050')


def test_exit_tray_stops_icon_without_touching_server():
    fake_icon = MagicMock()
    exit_tray(fake_icon, None)
    fake_icon.stop.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `D:\DocVault\venv\Scripts\python.exe -m pytest tests/test_tray_app.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_menu' from 'tray.tray_app'`

- [ ] **Step 3: Implement the menu and callbacks**

Append to `tray/tray_app.py` (after the existing imports, add `webbrowser` and `pystray`; after `get_server_url`, add):
```python
import webbrowser

import pystray


def open_docvault(icon=None, item=None):
    webbrowser.open(get_server_url())


def exit_tray(icon, item):
    icon.stop()


def build_menu():
    return pystray.Menu(
        pystray.MenuItem('Open DocVault', open_docvault, default=True),
        pystray.MenuItem('Exit', exit_tray),
    )
```

The full top of the file's import block should now read:
```python
"""DocVault system tray helper."""
import configparser
import os
import webbrowser

import pystray
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `D:\DocVault\venv\Scripts\python.exe -m pytest tests/test_tray_app.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tray/tray_app.py tests/test_tray_app.py
git commit -m "feat: add tray menu with Open DocVault and Exit actions"
```

---

### Task 3: Entry point wiring and manual smoke test

**Files:**
- Modify: `tray/tray_app.py`

**Interfaces:**
- Consumes: `build_menu()` from Task 2, the `.ico` asset at `frontend/static/tray_icon.ico` (already created).
- Produces: `main()` — the script's real entry point, not unit-tested (it blocks in a live GUI loop). Verified manually in Step 2 below.

- [ ] **Step 1: Implement `main()`**

Append to `tray/tray_app.py`:
```python
from PIL import Image

ICON_PATH = os.path.join(PROJECT_ROOT, 'frontend', 'static', 'tray_icon.ico')


def main():
    image = Image.open(ICON_PATH)
    icon = pystray.Icon('DocVault', image, 'DocVault', build_menu())
    icon.run()


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Manual smoke test**

Run: `D:\DocVault\venv\Scripts\pythonw.exe D:\DocVault\tray\tray_app.py`

Expected, checked in order:
1. No console window appears (pythonw.exe is windowless).
2. Within a couple seconds, the detective icon appears in the system tray (check the hidden-icons overflow arrow if not immediately visible on the taskbar).
3. Right-click the icon: a menu with exactly "Open DocVault" and "Exit" appears.
4. Double-click the icon: your default browser opens to the DocVault URL (or shows a connection-refused page if the server isn't running yet — that's expected, the tray never starts the server).
5. Click "Exit": the tray icon disappears. Confirm the process exited: `Get-Process pythonw -ErrorAction SilentlyContinue` should no longer list this instance.

- [ ] **Step 3: Commit**

```bash
git add tray/tray_app.py
git commit -m "feat: wire up tray icon entry point"
```

---

### Task 4: Scheduled task registration + end-to-end verification

**Files:**
- Create: `register_autostart.ps1`

**Interfaces:**
- Consumes: `start.ps1` (unmodified, existing file at project root), `tray/tray_app.py` from Task 3, `venv/Scripts/pythonw.exe` (existing venv).
- Produces: two Windows Scheduled Tasks named `DocVault-Server` and `DocVault-Tray`.

- [ ] **Step 1: Implement `register_autostart.ps1`**

Create `register_autostart.ps1` in the project root:
```powershell
#Requires -Version 5.1
<#
.SYNOPSIS
    Registers (or updates) the two Windows Scheduled Tasks that auto-start
    DocVault and its tray icon at logon. Safe to re-run any time (e.g.
    after moving the project to a new drive/path) - it updates existing
    tasks in place rather than erroring or duplicating.
.EXAMPLE
    .\register_autostart.ps1
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "    [OK] $msg" -ForegroundColor Green }

function Register-OrUpdateTask {
    param(
        [string]$TaskName,
        [string]$Program,
        [string]$Arguments
    )
    $action    = New-ScheduledTaskAction -Execute $Program -Argument $Arguments -WorkingDirectory $scriptDir
    $trigger   = New-ScheduledTaskTrigger -AtLogOn
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
        Write-OK "Updated existing task: $TaskName"
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
        Write-OK "Created task: $TaskName"
    }
}

Write-Step "Registering DocVault-Server task"
Register-OrUpdateTask -TaskName 'DocVault-Server' `
    -Program 'powershell.exe' `
    -Arguments "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptDir\start.ps1`""

Write-Step "Registering DocVault-Tray task"
$pythonw = Join-Path $scriptDir 'venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "pythonw.exe not found at $pythonw - run setup.ps1 first."
}
Register-OrUpdateTask -TaskName 'DocVault-Tray' `
    -Program $pythonw `
    -Arguments "`"$scriptDir\tray\tray_app.py`""

Write-Host ""
Write-Host "  Done. Both tasks will run at next logon." -ForegroundColor Green
Write-Host "  To test now without logging off, run:" -ForegroundColor Green
Write-Host "    Start-ScheduledTask -TaskName 'DocVault-Server'" -ForegroundColor Gray
Write-Host "    Start-ScheduledTask -TaskName 'DocVault-Tray'" -ForegroundColor Gray
Write-Host ""
```

- [ ] **Step 2: Register the tasks and verify they exist**

Run: `D:\DocVault\register_autostart.ps1`
Expected output: `[OK] Created task: DocVault-Server` and `[OK] Created task: DocVault-Tray`

Run: `Get-ScheduledTask -TaskName 'DocVault-Server','DocVault-Tray' | Select-Object TaskName, State`
Expected: both tasks listed, `State` is `Ready`

- [ ] **Step 3: Verify idempotent re-run (update path)**

Run: `D:\DocVault\register_autostart.ps1` again
Expected output: `[OK] Updated existing task: DocVault-Server` and `[OK] Updated existing task: DocVault-Tray` (not "Created" — proves the update branch works, not just the create branch)

- [ ] **Step 4: Verify tasks actually launch correctly without waiting for a real logon**

Run:
```powershell
Start-ScheduledTask -TaskName 'DocVault-Server'
Start-ScheduledTask -TaskName 'DocVault-Tray'
```
Expected, checked over the next ~30 seconds:
1. No console windows appear.
2. `Get-Process pythonw,python -ErrorAction SilentlyContinue` shows running processes.
3. The tray icon appears in the system tray.
4. `http://localhost:8050` becomes reachable in a browser once Ollama + the server finish their normal startup sequence.
5. Right-click the tray icon → "Open DocVault" opens the browser correctly; "Exit" removes only the tray icon, and the server is still reachable afterward.

- [ ] **Step 5: Full logon-cycle verification**

Log off and back on (or reboot).
Expected: both the server and tray icon come up automatically with no manual action and no visible console window at any point, matching Step 4's checks.

- [ ] **Step 6: Commit**

```bash
git add register_autostart.ps1
git commit -m "feat: add idempotent Task Scheduler registration for autostart + tray"
```
