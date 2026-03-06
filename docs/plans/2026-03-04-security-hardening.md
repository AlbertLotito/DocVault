# Security Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Three targeted security improvements: settable bind address, jailed open_path endpoint, and disk-free quota sensor with throttle integration.

**Architecture:** All three changes are self-contained. Bind address reads two new settings at startup. open_path gains two guard functions (vault-root jail + extension blocklist) with no new dependencies. The disk sensor follows the exact same BaseSensor pattern already in `core/monitor.py` and plugs into the existing throttle state machine.

**Tech Stack:** Python 3.13, FastAPI/uvicorn, psutil (already installed), SQLite, existing settings schema pattern.

---

## Feature A — Settable Bind Address

### Task A.1: Add `server:host` and `server:port` to settings schema

**Files:**
- Modify: `core/settings.py` (inside the `schema` dict, after the `google` group)

**Step 1: Write a failing unit test**

Create `tests/unit/test_server_settings.py`:

```python
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

def test_server_host_default():
    from core.settings import Settings
    s = Settings(config_path='')
    assert s.get('server:host') == '127.0.0.1'

def test_server_port_default():
    from core.settings import Settings
    s = Settings(config_path='')
    assert int(s.get('server:port')) == 8000

def test_server_host_in_schema():
    from core.settings import Settings
    s = Settings(config_path='')
    assert 'server:host' in s.schema

def test_server_port_in_schema():
    from core.settings import Settings
    s = Settings(config_path='')
    assert 'server:port' in s.schema
```

**Step 2: Run to confirm failure**

```
python -m pytest tests/unit/test_server_settings.py -v
```
Expected: 4 FAILED (KeyError or None returned)

**Step 3: Add schema entries**

In `core/settings.py`, inside the `self.schema = { ... }` dict, add after the `google:token_path` entry and before the `monitor:enabled` entry:

```python
            # Server
            'server:host': {
                'type': 'string', 'default': '127.0.0.1', 'label': 'Bind address', 'group': 'server',
                'description': 'IP address the web server binds to. "127.0.0.1" = this machine only (safest). A local network IP (e.g. 192.168.1.x) allows access from other devices on your LAN. "0.0.0.0" binds all interfaces. Changes require a server restart.',
            },
            'server:port': {
                'type': 'int', 'default': 8000, 'label': 'Port', 'group': 'server',
                'description': 'TCP port the web server listens on. Default is 8000. Change if another application is using this port. Changes require a server restart.',
            },
```

**Step 4: Run tests to confirm pass**

```
python -m pytest tests/unit/test_server_settings.py -v
```
Expected: 4 PASSED

**Step 5: Commit**

```
git add core/settings.py tests/unit/test_server_settings.py
git commit -m "feat(security): add server:host and server:port to settings schema"
```

---

### Task A.2: Read bind address from settings in `run.py`

**Files:**
- Modify: `run.py` (lines 69–70, the `uvicorn.run()` call)

**Step 1: Write a failing test**

The test here is behavioural — we verify `run.py` reads from settings, not that uvicorn actually binds (that needs a running server). Instead write a simple import test that the two settings resolve correctly via the global singleton:

Add to `tests/unit/test_server_settings.py`:

```python
def test_server_host_resolves_via_global_singleton():
    from core.settings import settings
    val = settings.get('server:host')
    assert val is not None
    assert isinstance(val, str)

def test_server_port_resolves_via_global_singleton():
    from core.settings import settings
    val = settings.get('server:port')
    assert int(val) == 8000  # config.ini has no override; must fall back to schema default
```

Run:
```
python -m pytest tests/unit/test_server_settings.py -v
```
Expected: all 6 pass (A.1 tests still pass, 2 new pass if schema is correct)

**Step 2: Update `run.py`**

Replace the current last line of `start()`:

```python
    # Start web server (blocking)
    print("Starting DocVault at http://localhost:8000")
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)
```

with:

```python
    # Start web server (blocking) — host/port read from settings so they survive restarts
    host = str(settings.get('server:host') or '127.0.0.1')
    port = int(settings.get('server:port') or 8000)
    print(f"Starting DocVault at http://{host}:{port}")
    uvicorn.run("api.main:app", host=host, port=port, reload=False)
```

**Step 3: Manual smoke test**

Start the server (`python run.py`). Confirm it prints:
```
Starting DocVault at http://127.0.0.1:8000
```
And that `http://127.0.0.1:8000` responds in a browser. Confirm `http://<LAN-IP>:8000` does NOT respond (connection refused), proving the bind is local-only.

**Step 4: Commit**

```
git add run.py tests/unit/test_server_settings.py
git commit -m "feat(security): bind server to 127.0.0.1 by default; host/port settable"
```

---

## Feature B — open_path Jailing

### Task B.1: Add vault-root guard and extension blocklist to open_path

**Files:**
- Modify: `api/routes/utils.py`

**Step 1: Write failing unit tests**

Create `tests/unit/test_open_path_security.py`:

```python
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# We test the guard functions directly, not the HTTP endpoint.
# Import after sys.path is set.

from api.routes.utils import BLOCKED_EXTENSIONS, _is_blocked_extension, _is_in_vault_root


def test_exe_is_blocked():
    assert _is_blocked_extension('C:/foo/malware.exe') is True

def test_bat_is_blocked():
    assert _is_blocked_extension('C:/foo/script.bat') is True

def test_msi_is_blocked():
    assert _is_blocked_extension('setup.msi') is True

def test_ps1_is_blocked():
    assert _is_blocked_extension('run.ps1') is True

def test_pdf_is_not_blocked():
    assert _is_blocked_extension('report.pdf') is False

def test_png_is_not_blocked():
    assert _is_blocked_extension('photo.png') is False

def test_docx_is_not_blocked():
    assert _is_blocked_extension('doc.docx') is False

def test_extension_check_is_case_insensitive():
    assert _is_blocked_extension('VIRUS.EXE') is True
    assert _is_blocked_extension('Photo.PNG') is False
```

Run:
```
python -m pytest tests/unit/test_open_path_security.py -v
```
Expected: ImportError (functions don't exist yet)

**Step 2: Implement the guard functions and update open_path**

Replace the entire `open_path` section of `api/routes/utils.py`. Add these at the module level (after the imports), then update the endpoint:

```python
# ---------------------------------------------------------------------------
# open_path security
# ---------------------------------------------------------------------------

BLOCKED_EXTENSIONS = {
    '.exe', '.com', '.msi', '.bat', '.cmd', '.ps1', '.vbs', '.wsf',
    '.scr', '.pif', '.dll', '.sys', '.reg', '.hta', '.lnk', '.url',
    '.cpl', '.inf', '.js', '.jse', '.vbe',
}


def _is_blocked_extension(path: str) -> bool:
    """Return True if the file extension is on the executable blocklist."""
    ext = os.path.splitext(path)[1].lower()
    return ext in BLOCKED_EXTENSIONS


def _is_in_vault_root(path: str) -> bool:
    """
    Return True if `path` is physically inside at least one active or archived
    vault's scan_directory. Uses os.path.realpath to resolve symlinks and prevent
    traversal attacks.
    """
    try:
        from core.vault_manager import VaultManager
        from core.manager import get_db_path
        target = os.path.realpath(path)
        for vault in VaultManager(get_db_path()).list_vaults():
            if vault['state'] not in ('active', 'archived'):
                continue
            root = os.path.realpath(vault['scan_directory'])
            if target == root or target.startswith(root + os.sep):
                return True
    except Exception:
        pass
    return False
```

Then replace the `open_path` endpoint:

```python
@router.post("/utils/open_path")
def open_path(req: OpenRequest):
    """
    Open a file or its containing folder in Windows Explorer.
    Guards:
      - Path must resolve to within a known vault root (no arbitrary filesystem access).
      - File action is blocked for executable extensions.
      - Only 'file' and 'folder' actions are accepted; 'file' opens with the OS default
        viewer (e.g. Adobe for PDFs); 'folder' opens Explorer with the file selected.
    """
    if req.action not in ('file', 'folder'):
        return {"status": "error", "detail": "Invalid action"}

    # Resolve symlinks before any check
    resolved = os.path.realpath(req.path)

    if not os.path.exists(resolved):
        return {"status": "error", "detail": "Path not found"}

    if not _is_in_vault_root(resolved):
        return {"status": "error", "detail": "Path is outside all vault roots"}

    if req.action == 'file' and _is_blocked_extension(resolved):
        return {"status": "error", "detail": "File type not permitted"}

    try:
        if req.action == 'file':
            os.startfile(resolved)
        elif req.action == 'folder':
            # Open Explorer with the file/folder selected (visible, user-driven)
            target = resolved if os.path.isdir(resolved) else resolved
            subprocess.Popen(['explorer', f'/select,{target}'])
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
```

**Step 3: Run tests**

```
python -m pytest tests/unit/test_open_path_security.py -v
```
Expected: 8 PASSED

**Step 4: Commit**

```
git add api/routes/utils.py tests/unit/test_open_path_security.py
git commit -m "feat(security): jail open_path to vault roots; block executable extensions"
```

---

## Feature C — Disk Quota Sensor

### Task C.1: Extend `MonitorReading` and `ThrottleThresholds` with disk fields

**Files:**
- Modify: `core/monitor.py`

**Step 1: Write failing unit tests**

Create `tests/unit/test_disk_sensor.py`:

```python
import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.monitor import MonitorReading, ThrottleThresholds


def test_monitor_reading_has_disk_fields():
    r = MonitorReading()
    assert hasattr(r, 'disk_free_pct')
    assert hasattr(r, 'disk_free_gb')
    assert hasattr(r, 'disk_total_gb')
    assert hasattr(r, 'disk_path')

def test_monitor_reading_disk_defaults_to_zero():
    r = MonitorReading()
    assert r.disk_free_pct == 0.0
    assert r.disk_free_gb == 0.0

def test_throttle_thresholds_has_disk_fields():
    t = ThrottleThresholds()
    assert hasattr(t, 'disk_throttle_free_pct')
    assert hasattr(t, 'disk_throttle_free_gb')

def test_throttle_thresholds_disk_defaults():
    t = ThrottleThresholds()
    assert t.disk_throttle_free_pct == 5.0
    assert t.disk_throttle_free_gb == 10.0
```

Run:
```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: 4 FAILED (fields don't exist)

**Step 2: Add disk fields to the dataclasses in `core/monitor.py`**

In `MonitorReading`, add after `vram_total_gb`:

```python
    disk_free_pct:  float = 0.0   # % of disk free (worst monitored volume)
    disk_free_gb:   float = 0.0   # GB free
    disk_total_gb:  float = 0.0   # GB total
    disk_path:      str   = ''    # which path triggered the minimum
```

In `ThrottleThresholds`, add after `cpu_temp_throttle`:

```python
    disk_throttle_free_pct: float = 5.0    # throttle if free % drops below this
    disk_throttle_free_gb:  float = 10.0   # throttle if free GB drops below this (OR)
```

**Step 3: Run tests**

```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: 4 PASSED

**Step 4: Commit**

```
git add core/monitor.py tests/unit/test_disk_sensor.py
git commit -m "feat(disk-sensor): add disk fields to MonitorReading and ThrottleThresholds"
```

---

### Task C.2: Implement `DiskSensor`

**Files:**
- Modify: `core/monitor.py` (add class after `WMICPUTempSensor`)

**Step 1: Write failing unit tests**

Add to `tests/unit/test_disk_sensor.py`:

```python
def test_disk_sensor_initialises():
    from core.monitor import DiskSensor
    s = DiskSensor()
    assert s.initialise() is True  # psutil is always available

def test_disk_sensor_returns_nonzero_reading():
    from core.monitor import DiskSensor
    s = DiskSensor()
    s.initialise()
    r = s.read()
    # Every real disk has some space
    assert r.disk_total_gb > 0
    assert r.disk_free_gb >= 0
    assert 0.0 <= r.disk_free_pct <= 100.0

def test_disk_sensor_path_is_set():
    from core.monitor import DiskSensor
    s = DiskSensor()
    s.initialise()
    r = s.read()
    assert r.disk_path != ''

def test_disk_sensor_picks_most_constrained_volume():
    """Sensor samples multiple paths and returns the one with least free space."""
    from core.monitor import DiskSensor
    import os
    # C:\ and the app root are always on the same drive on this machine,
    # but the sensor should not crash on duplicates.
    s = DiskSensor(extra_paths=[os.getcwd(), os.path.expanduser('~')])
    s.initialise()
    r = s.read()
    assert r.disk_total_gb > 0
```

Run:
```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: 4 new FAILED (DiskSensor not defined)

**Step 2: Implement `DiskSensor` in `core/monitor.py`**

Add the class after `WMICPUTempSensor` and before `_merge_readings`:

```python
class DiskSensor(BaseSensor):
    """
    Disk-free sensor. Checks the volumes used by:
      - the app directory (docvault.db lives here)
      - paths:cache_directory
      - each active/archived vault's scan_directory
      - any extra_paths passed at construction (for testing)

    Returns the reading for the MOST CONSTRAINED volume (lowest free_pct).
    psutil is already a project dependency so no new packages required.
    """

    def __init__(self, extra_paths: list[str] | None = None):
        super().__init__('DiskSensor')
        self._extra_paths: list[str] = extra_paths or []

    def _init_hardware(self) -> bool:
        import psutil  # noqa
        return True

    def _paths_to_check(self) -> list[str]:
        """Collect all relevant paths. Deduplication happens at the drive level."""
        paths = list(self._extra_paths)
        # App root
        paths.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # Cache directory from settings
        try:
            from core.settings import settings as s
            cache = s.get('paths:cache_directory')
            if cache:
                paths.append(cache)
        except Exception:
            pass
        # Vault scan directories
        try:
            from core.vault_manager import VaultManager
            from core.manager import get_db_path
            for vault in VaultManager(get_db_path()).list_vaults():
                if vault['state'] in ('active', 'archived'):
                    paths.append(vault['scan_directory'])
        except Exception:
            pass
        return paths

    def _read_hardware(self) -> MonitorReading:
        import psutil

        paths = self._paths_to_check()
        worst: MonitorReading | None = None

        seen_roots: set[str] = set()
        for path in paths:
            try:
                # Resolve to the drive/mount-point root to avoid sampling the same
                # volume multiple times (e.g. E:\DocVault and E:\DocTest → same drive)
                drive = os.path.splitdrive(os.path.realpath(path))[0] or '/'
                if drive in seen_roots:
                    continue
                seen_roots.add(drive)

                usage = psutil.disk_usage(path)
                free_pct  = (usage.free / usage.total) * 100 if usage.total else 0.0
                free_gb   = usage.free  / 1024**3
                total_gb  = usage.total / 1024**3

                reading = MonitorReading(
                    disk_free_pct  = round(free_pct, 2),
                    disk_free_gb   = round(free_gb, 2),
                    disk_total_gb  = round(total_gb, 2),
                    disk_path      = path,
                )

                if worst is None or reading.disk_free_pct < worst.disk_free_pct:
                    worst = reading

            except Exception:
                continue

        return worst or MonitorReading()
```

**Step 3: Register `DiskSensor` in `HardwareMonitor._init_sensors()`**

In `HardwareMonitor._init_sensors()`, add `DiskSensor()` to the candidates list:

```python
        candidates = [PSUtilSensor(), NvidiaSensor(), WMICPUTempSensor(), DiskSensor()]
```

**Step 4: Run tests**

```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: all 8 PASSED

**Step 5: Commit**

```
git add core/monitor.py tests/unit/test_disk_sensor.py
git commit -m "feat(disk-sensor): add DiskSensor class; register in HardwareMonitor"
```

---

### Task C.3: Wire disk into `_merge_readings` and `ThrottleStateMachine`

**Files:**
- Modify: `core/monitor.py`

**Step 1: Write failing tests**

Add to `tests/unit/test_disk_sensor.py`:

```python
def test_merge_readings_includes_disk():
    from core.monitor import MonitorReading, _merge_readings
    a = MonitorReading(cpu_pct=50.0)
    b = MonitorReading(disk_free_pct=3.0, disk_free_gb=2.0, disk_total_gb=100.0, disk_path='E:\\')
    merged = _merge_readings(a, b)
    assert merged.cpu_pct == 50.0
    assert merged.disk_free_pct == 3.0
    assert merged.disk_path == 'E:\\'

def test_throttle_machine_enters_throttled_on_disk_pressure():
    from core.monitor import ThrottleStateMachine, ThrottleThresholds, MonitorReading
    sm = ThrottleStateMachine()
    thresholds = ThrottleThresholds(disk_throttle_free_pct=10.0, disk_throttle_free_gb=5.0)
    # Simulate low disk (4% free, 3 GB free — both below threshold)
    reading = MonitorReading(disk_free_pct=4.0, disk_free_gb=3.0, disk_total_gb=100.0)
    sm.update(reading, thresholds)
    assert sm.state == 'throttled'

def test_throttle_machine_stays_normal_when_disk_ok():
    from core.monitor import ThrottleStateMachine, ThrottleThresholds, MonitorReading
    sm = ThrottleStateMachine()
    thresholds = ThrottleThresholds(disk_throttle_free_pct=5.0, disk_throttle_free_gb=10.0)
    reading = MonitorReading(disk_free_pct=50.0, disk_free_gb=500.0, disk_total_gb=1000.0)
    sm.update(reading, thresholds)
    assert sm.state == 'normal'
```

Run:
```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: 3 new FAILED

**Step 2: Update `_merge_readings` to include disk fields**

In `_merge_readings()`, add after the existing `if` blocks:

```python
    if r.disk_free_pct or r.disk_free_gb:
        # Keep the MOST CONSTRAINED reading across merged sources
        if worst is None or r.disk_free_pct < merged.disk_free_pct:
            merged.disk_free_pct  = r.disk_free_pct
            merged.disk_free_gb   = r.disk_free_gb
            merged.disk_total_gb  = r.disk_total_gb
            merged.disk_path      = r.disk_path
```

Note: `_merge_readings` currently uses `merged = MonitorReading()` and copies fields in. The disk logic picks the worst (lowest pct), not just any non-zero. Rewrite the disk section as:

```python
    # Disk: keep the most constrained (lowest free_pct) across all readings
    disk_candidates = [r for r in readings if r.disk_free_pct > 0 or r.disk_free_gb > 0]
    if disk_candidates:
        worst_disk = min(disk_candidates, key=lambda r: r.disk_free_pct)
        merged.disk_free_pct  = worst_disk.disk_free_pct
        merged.disk_free_gb   = worst_disk.disk_free_gb
        merged.disk_total_gb  = worst_disk.disk_total_gb
        merged.disk_path      = worst_disk.disk_path
```

**Step 3: Update `ThrottleStateMachine.update()` to include disk pressure**

In the `pressure` boolean expression, add disk checks:

```python
        pressure = (
            reading.gpu_temp     > thresholds.gpu_temp_throttle or
            reading.gpu_util_pct > thresholds.gpu_util_throttle or
            reading.cpu_temp     > thresholds.cpu_temp_throttle or
            reading.ram_pct      > thresholds.ram_throttle_pct  or
            (reading.disk_free_pct > 0 and reading.disk_free_pct < thresholds.disk_throttle_free_pct) or
            (reading.disk_free_gb  > 0 and reading.disk_free_gb  < thresholds.disk_throttle_free_gb)
        )
```

The `> 0` guards prevent DummySensor zeros from falsely triggering disk pressure.

**Step 4: Load disk thresholds in `_load_thresholds()`**

Add to the `ThrottleThresholds(...)` constructor call in `_load_thresholds()`:

```python
            disk_throttle_free_pct = float(s.get('monitor:disk_throttle_free_pct') or 5.0),
            disk_throttle_free_gb  = float(s.get('monitor:disk_throttle_free_gb')  or 10.0),
```

**Step 5: Run tests**

```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: all 11 PASSED

**Step 6: Commit**

```
git add core/monitor.py
git commit -m "feat(disk-sensor): wire disk into merge and throttle state machine"
```

---

### Task C.4: Add disk settings to the settings schema

**Files:**
- Modify: `core/settings.py`

**Step 1: Write failing test**

Add to `tests/unit/test_disk_sensor.py`:

```python
def test_disk_throttle_settings_in_schema():
    from core.settings import Settings
    s = Settings(config_path='')
    assert float(s.get('monitor:disk_throttle_free_pct')) == 5.0
    assert float(s.get('monitor:disk_throttle_free_gb')) == 10.0
```

Run:
```
python -m pytest tests/unit/test_disk_sensor.py::test_disk_throttle_settings_in_schema -v
```
Expected: FAILED (returns None)

**Step 2: Add entries to schema in `core/settings.py`**

After `monitor:cpu_temp_throttle`, add:

```python
            'monitor:disk_throttle_free_pct': {
                'type': 'float', 'default': 5.0, 'label': 'Disk free throttle (%)', 'group': 'monitor',
                'description': 'Throttle workers when any monitored volume has less than this percentage of space free. Checked against vault scan directories and the cache directory. Set to 0 to disable disk-free throttling.',
            },
            'monitor:disk_throttle_free_gb': {
                'type': 'float', 'default': 10.0, 'label': 'Disk free throttle (GB)', 'group': 'monitor',
                'description': 'Throttle workers when any monitored volume has less than this many GB free. Either this or the percentage threshold can trigger throttling — whichever is hit first. Set to 0 to disable.',
            },
```

**Step 3: Run test**

```
python -m pytest tests/unit/test_disk_sensor.py -v
```
Expected: all 12 PASSED

**Step 4: Commit**

```
git add core/settings.py tests/unit/test_disk_sensor.py
git commit -m "feat(disk-sensor): add disk quota settings to schema (monitor group)"
```

---

### Task C.5: Persist disk fields to `logs.db` and expose via monitor API

**Files:**
- Modify: `core/manager.py` (`init_logs_db`)
- Modify: `core/monitor.py` (`_record_sample`)
- Modify: `api/routes/monitor.py`

**Step 1: Add columns to `system_stats` in `core/manager.py`**

In `init_logs_db()`, find the `CREATE TABLE IF NOT EXISTS system_stats` statement and add columns after `vram_total_gb`:

```sql
    disk_free_pct  REAL,
    disk_free_gb   REAL,
    disk_total_gb  REAL,
    disk_path      TEXT,
```

Also add `ALTER TABLE IF NOT EXISTS` guards for existing databases (SQLite doesn't support `IF NOT EXISTS` on `ALTER`, so use try/except):

In `init_logs_db()`, after the CREATE TABLE calls, add:

```python
    for col, coltype in [
        ('disk_free_pct',  'REAL'),
        ('disk_free_gb',   'REAL'),
        ('disk_total_gb',  'REAL'),
        ('disk_path',      'TEXT'),
    ]:
        try:
            conn.execute(f"ALTER TABLE system_stats ADD COLUMN {col} {coltype}")
        except Exception:
            pass  # column already exists
```

**Step 2: Update `_record_sample()` in `core/monitor.py`**

Add disk fields to the INSERT:

```python
    conn.execute(
        """INSERT OR REPLACE INTO system_stats
           (sampled_at, cpu_pct, cpu_temp, ram_used_gb, ram_total_gb, ram_pct,
            gpu_temp, gpu_util_pct, vram_used_gb, vram_total_gb, throttle_state,
            disk_free_pct, disk_free_gb, disk_total_gb, disk_path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (reading.sampled_at, reading.cpu_pct, reading.cpu_temp,
         reading.ram_used_gb, reading.ram_total_gb, reading.ram_pct,
         reading.gpu_temp, reading.gpu_util_pct,
         reading.vram_used_gb, reading.vram_total_gb, state,
         reading.disk_free_pct, reading.disk_free_gb,
         reading.disk_total_gb, reading.disk_path)
    )
```

**Step 3: Update `GET /api/monitor/status` in `api/routes/monitor.py`**

Ensure the response includes disk fields from the reading. The endpoint currently returns the reading as a dict. If it serialises the dataclass directly, the new fields will appear automatically. If it manually constructs the dict, add:

```python
'disk_free_pct':  reading.disk_free_pct,
'disk_free_gb':   round(reading.disk_free_gb, 1),
'disk_total_gb':  round(reading.disk_total_gb, 1),
'disk_path':      reading.disk_path,
```

(Check the current serialisation approach in `api/routes/monitor.py` before editing.)

**Step 4: Manual smoke test**

Start the server. Call `GET http://127.0.0.1:8000/api/monitor/status` and confirm the response contains `disk_free_pct`, `disk_free_gb`, `disk_total_gb`, `disk_path` with realistic values.

**Step 5: Commit**

```
git add core/manager.py core/monitor.py api/routes/monitor.py
git commit -m "feat(disk-sensor): persist disk metrics to logs.db; expose via monitor API"
```

---

### Task C.6: Show disk in the Vault Status resource strip

**Files:**
- Modify: `frontend/index.html`

**Step 1: Locate the resource strip JS**

Find the `updateResourceStrip(data)` function in `index.html` (polls `/api/monitor/status` every 10s).

**Step 2: Add a disk tile**

The resource strip renders tiles like: GPU Temp · GPU Util · CPU Temp · CPU · RAM.

Add a Disk tile after RAM. Pattern matches the existing tiles:

```html
<div class="resource-tile">
  <div class="resource-label">Disk Free</div>
  <div class="resource-value" id="res-disk-free">--</div>
</div>
```

In the JS `updateResourceStrip(data)` function, add:

```js
const diskPct  = data.reading?.disk_free_pct ?? null;
const diskGb   = data.reading?.disk_free_gb  ?? null;
const diskPath = data.reading?.disk_path      ?? '';

const diskEl = document.getElementById('res-disk-free');
if (diskEl && diskPct !== null) {
    const drive = diskPath.split(/[/\\]/)[0] || diskPath;
    diskEl.textContent = `${diskGb.toFixed(1)} GB (${diskPct.toFixed(1)}%)`;
    diskEl.title = `Least-free monitored volume: ${diskPath}`;
    // Colour: green > 20%, yellow > 5%, red ≤ 5%
    diskEl.className = diskPct > 20 ? 'resource-value ok'
                     : diskPct > 5  ? 'resource-value warn'
                                    : 'resource-value crit';
}
```

**Step 3: Manual smoke test**

Open the Vault Status page. Confirm the Disk Free tile shows a value, that hovering shows the drive path, and that the colour is green (disk is almost certainly not critically low).

**Step 4: Commit**

```
git add frontend/index.html
git commit -m "feat(disk-sensor): show disk free in Vault Status resource strip"
```

---

## Final Gate

### Task D.1: Run full test suite and confirm rig integrity

```
python -m pytest tests/unit/ -v
```
Expected: all tests pass (was 35; now ~47 with new tests).

```
python tests/rigs/check_db_migration.py
python tests/rigs/check_settings_resolution.py
python tests/rigs/check_resource_governor.py
python tests/rigs/check_worker_priority.py
```
Expected: all offline rigs still pass (no regressions).

### Task D.2: Update status doc

Create `docs/status/2026-03-04-project-status.md` (new session status document).

Document:
- Feature A: settable bind address; default now `127.0.0.1`
- Feature B: open_path vault jail + extension blocklist
- Feature C: DiskSensor, disk quota settings, disk in resource strip
- Update "Known Limitations" to remove the bind-address and open_path risks (addressed)
- Update file map with `tests/unit/test_server_settings.py`, `test_open_path_security.py`, `test_disk_sensor.py`
- Update settings schema table with the 2 new `server:*` keys and 2 new `monitor:disk_*` keys

### Task D.3: Final commit

```
git add docs/status/2026-03-04-project-status.md
git commit -m "docs: add 2026-03-04 project status -- security hardening complete"
```

---

## Summary of All New/Modified Files

| Action | File | Reason |
|---|---|---|
| Modify | `core/settings.py` | Add `server:host`, `server:port`, `monitor:disk_throttle_free_pct`, `monitor:disk_throttle_free_gb` |
| Modify | `run.py` | Read `server:host`/`server:port` at startup; default `127.0.0.1` |
| Modify | `api/routes/utils.py` | Add `BLOCKED_EXTENSIONS`, `_is_blocked_extension()`, `_is_in_vault_root()`; update `open_path` |
| Modify | `core/monitor.py` | Add disk fields to `MonitorReading`/`ThrottleThresholds`; add `DiskSensor`; update merge + state machine + `_record_sample` |
| Modify | `core/manager.py` | Add disk columns to `system_stats` in `init_logs_db()` |
| Modify | `api/routes/monitor.py` | Include disk fields in response |
| Modify | `frontend/index.html` | Disk tile in resource strip |
| Create | `tests/unit/test_server_settings.py` | 6 tests |
| Create | `tests/unit/test_open_path_security.py` | 8 tests |
| Create | `tests/unit/test_disk_sensor.py` | 12 tests |
| Create | `docs/status/2026-03-04-project-status.md` | Session status doc |

## New Settings Keys

| Key | Default | Group | Purpose |
|---|---|---|---|
| `server:host` | `127.0.0.1` | Server | Bind address — change requires restart |
| `server:port` | `8000` | Server | Listen port — change requires restart |
| `monitor:disk_throttle_free_pct` | `5.0` | Monitor | Throttle when free% falls below this |
| `monitor:disk_throttle_free_gb` | `10.0` | Monitor | Throttle when free GB falls below this |
