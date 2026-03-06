"""
core/monitor.py -- Resource governor for DocVault.

Samples hardware metrics once per minute and adaptively throttles
the worker pool. Extendable sensor registry -- add new GPU/CPU vendors
by subclassing BaseSensor.

Throttle states: normal -> throttled -> cooldown -> (retest) -> normal
"""

from __future__ import annotations
import threading
import time
import warnings
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from core import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MonitorReading:
    """Combined hardware reading from all sensors."""
    cpu_pct:       float = 0.0
    cpu_temp:      float = 0.0
    ram_used_gb:   float = 0.0
    ram_total_gb:  float = 0.0
    ram_pct:       float = 0.0
    gpu_temp:      float = 0.0
    gpu_util_pct:  float = 0.0
    vram_used_gb:  float = 0.0
    vram_total_gb: float = 0.0
    disk_free_gb:  float = 0.0
    disk_free_pct: float = 0.0
    sampled_at:    str   = ''
    throttle_reason: str = ''

    def __post_init__(self):
        if not self.sampled_at:
            self.sampled_at = datetime.now(timezone.utc).isoformat()


@dataclass
class ThrottleThresholds:
    """All thresholds configurable via settings. Defaults match design doc."""
    gpu_temp_throttle:  float = 80.0
    gpu_temp_cooldown:  float = 88.0
    gpu_util_throttle:  float = 70.0
    sustained_minutes:  int   = 3
    cooldown_minutes:   int   = 10
    ram_throttle_pct:   float = 85.0
    cpu_temp_throttle:  float = 85.0


# ---------------------------------------------------------------------------
# Sensor abstraction
# ---------------------------------------------------------------------------

class BaseSensor(ABC):
    """Abstract hardware sensor. Return zeros on failure; never raise."""

    def __init__(self, name: str):
        self.name = name
        self.available = False

    @abstractmethod
    def _init_hardware(self) -> bool:
        """Attempt hardware initialisation. Return True if successful."""
        ...

    @abstractmethod
    def _read_hardware(self) -> MonitorReading:
        """Read hardware metrics. Called only if available=True."""
        ...

    def initialise(self) -> bool:
        """Try to initialise. On failure, log warning -- not an error."""
        try:
            self.available = self._init_hardware()
        except Exception as e:
            warnings.warn(f"[monitor] {self.name} sensor failed to init: {e} -- using dummy")
            self.available = False
        return self.available

    def read(self) -> MonitorReading:
        if not self.available:
            return MonitorReading()
        try:
            return self._read_hardware()
        except Exception:
            return MonitorReading()


class DummySensor(BaseSensor):
    """Fallback sensor returning all zeros. Used when hardware sensor fails to init."""

    def _init_hardware(self) -> bool:
        return True  # dummy always available

    def _read_hardware(self) -> MonitorReading:
        return MonitorReading()


class NvidiaSensor(BaseSensor):
    """NVIDIA GPU sensor via pynvml."""

    def __init__(self):
        super().__init__('NvidiaSensor')
        self._handle = None

    def _init_hardware(self) -> bool:
        import pynvml
        pynvml.nvmlInit()
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return True

    def _read_hardware(self) -> MonitorReading:
        import pynvml
        temp     = pynvml.nvmlDeviceGetTemperature(self._handle, pynvml.NVML_TEMPERATURE_GPU)
        util     = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        return MonitorReading(
            gpu_temp      = float(temp),
            gpu_util_pct  = float(util.gpu),
            vram_used_gb  = mem_info.used  / 1024**3,
            vram_total_gb = mem_info.total / 1024**3,
        )


class PSUtilSensor(BaseSensor):
    """CPU utilization and RAM via psutil."""

    def __init__(self):
        super().__init__('PSUtilSensor')

    def _init_hardware(self) -> bool:
        import psutil  # noqa -- just verify it imports
        return True

    def _read_hardware(self) -> MonitorReading:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=1)
        ram     = psutil.virtual_memory()
        return MonitorReading(
            cpu_pct      = cpu_pct,
            ram_used_gb  = ram.used  / 1024**3,
            ram_total_gb = ram.total / 1024**3,
            ram_pct      = ram.percent,
        )


class WMICPUTempSensor(BaseSensor):
    """CPU temperature via WMI (Windows only)."""

    def __init__(self):
        super().__init__('WMICPUTempSensor')

    def _init_hardware(self) -> bool:
        import wmi
        w = wmi.WMI(namespace='root/wmi')
        # Probe to check it works
        _ = w.MSAcpi_ThermalZoneTemperature()
        return True

    def _read_hardware(self) -> MonitorReading:
        import wmi
        w = wmi.WMI(namespace='root/wmi')
        temps = w.MSAcpi_ThermalZoneTemperature()
        if temps:
            # WMI returns temperature in tenths of Kelvin
            celsius = (temps[0].CurrentTemperature / 10.0) - 273.15
            return MonitorReading(cpu_temp=celsius)
        return MonitorReading()


class DiskSensor(BaseSensor):
    """Disk space metrics via psutil."""

    def __init__(self):
        super().__init__('DiskSensor')

    def _init_hardware(self) -> bool:
        import psutil
        return True

    def _read_hardware(self) -> MonitorReading:
        import psutil
        # Using root/cwd drive for quota check
        usage = psutil.disk_usage('.')
        return MonitorReading(
            disk_free_gb  = usage.free / 1024**3,
            disk_free_pct = (usage.free / usage.total) * 100,
        )


def _merge_readings(*readings: MonitorReading) -> MonitorReading:
    """Merge non-zero fields from multiple readings into one."""
    merged = MonitorReading()
    for r in readings:
        if r.cpu_pct:       merged.cpu_pct       = r.cpu_pct
        if r.cpu_temp:      merged.cpu_temp       = r.cpu_temp
        if r.ram_pct:       merged.ram_pct        = r.ram_pct
        if r.ram_used_gb:   merged.ram_used_gb    = r.ram_used_gb
        if r.ram_total_gb:  merged.ram_total_gb   = r.ram_total_gb
        if r.gpu_temp:      merged.gpu_temp       = r.gpu_temp
        if r.gpu_util_pct:  merged.gpu_util_pct   = r.gpu_util_pct
        if r.vram_used_gb:  merged.vram_used_gb   = r.vram_used_gb
        if r.vram_total_gb: merged.vram_total_gb  = r.vram_total_gb
        if r.disk_free_gb:  merged.disk_free_gb   = r.disk_free_gb
        if r.disk_free_pct: merged.disk_free_pct  = r.disk_free_pct
    return merged


# ---------------------------------------------------------------------------
# Throttle state machine
# ---------------------------------------------------------------------------

class ThrottleStateMachine:
    """
    Transitions:
        normal -> throttled   (pressure detected)
        throttled -> cooldown (pressure sustained > sustained_minutes)
        throttled -> normal   (pressure clears)
        cooldown -> normal    (after cooldown_minutes, if pressure gone)
        cooldown -> cooldown  (pressure still present after cooldown)
        normal -> cooldown    (critical threshold: gpu_temp > gpu_temp_cooldown)
    """

    def __init__(self):
        self.state: str = 'normal'
        self.reason: str = ''
        self._throttled_since: Optional[float] = None
        self._cooldown_until:  Optional[float] = None

    def update(self, reading: MonitorReading, thresholds: ThrottleThresholds):
        now = time.monotonic()

        # Immediate cooldown on critical GPU temp
        if reading.gpu_temp > thresholds.gpu_temp_cooldown:
            self._enter_cooldown(now, thresholds, f"CRITICAL: GPU Temp ({reading.gpu_temp}C) > Max ({thresholds.gpu_temp_cooldown}C)")
            return

        # Check triggers
        reasons = []
        if reading.gpu_temp > thresholds.gpu_temp_throttle:
            reasons.append(f"GPU Temp ({reading.gpu_temp}C)")
        if reading.gpu_util_pct > thresholds.gpu_util_throttle:
            reasons.append(f"GPU Load ({reading.gpu_util_pct}%)")
        if reading.cpu_temp > thresholds.cpu_temp_throttle:
            reasons.append(f"CPU Temp ({reading.cpu_temp}C)")
        if reading.ram_pct > thresholds.ram_throttle_pct:
            reasons.append(f"RAM Usage ({reading.ram_pct}%)")

        pressure = len(reasons) > 0
        pressure_text = "High " + ", ".join(reasons) if pressure else ""

        if self.state == 'normal':
            if pressure:
                self.state = 'throttled'
                self.reason = pressure_text
                self._throttled_since = now
            else:
                self.reason = ""

        elif self.state == 'throttled':
            if not pressure:
                self.state = 'normal'
                self.reason = ""
                self._throttled_since = None
            elif self._throttled_since and \
                 (now - self._throttled_since) >= thresholds.sustained_minutes * 60:
                self._enter_cooldown(now, thresholds, f"Sustained pressure: {pressure_text}")
            else:
                self.reason = pressure_text

        elif self.state == 'cooldown':
            if self._cooldown_until and now >= self._cooldown_until:
                if pressure:
                    self._enter_cooldown(now, thresholds, f"Pressure persists: {pressure_text}")
                else:
                    self.state = 'normal'
                    self.reason = ""
                    self._cooldown_until = None
                    self._throttled_since = None

    def _enter_cooldown(self, now: float, thresholds: ThrottleThresholds, reason: str):
        self.state = 'cooldown'
        self.reason = reason
        self._throttled_since = None
        self._cooldown_until = now + thresholds.cooldown_minutes * 60


# ---------------------------------------------------------------------------
# HardwareMonitor -- the daemon
# ---------------------------------------------------------------------------

# Shared throttle state (read by workers)
_throttle_state: str = 'normal'
_throttle_reason: str = ''
_throttle_lock = threading.Lock()
_last_reading: Optional[MonitorReading] = None
_last_user_activity: float = 0.0

# Ollama Governor State
_ollama_semaphore: Optional[threading.Semaphore] = None
_ollama_serial_lock = threading.Lock()
_ollama_limit: int = -1


def notify_user_activity():
    """Signalled by API when a user performs a search or interactive query."""
    global _last_user_activity
    with _throttle_lock:
        _last_user_activity = time.monotonic()


def get_throttle_state() -> str:
    """Returns (state, reason) tuple."""
    with _throttle_lock:
        state = _throttle_state
        reason = _throttle_reason
        
        # Hardware protection (cooldown) always wins
        if state == 'cooldown':
            return 'cooldown', reason
            
        # Check for search-triggered throttle
        if _last_user_activity > 0:
            try:
                from core.settings import settings
                duration = int(settings.get('monitor:search_throttle_duration') or 60)
                elapsed = time.monotonic() - _last_user_activity
                if elapsed < duration:
                    return 'throttled', f"User activity detected ({int(duration - elapsed)}s remaining)"
            except Exception:
                pass
                
        return state, reason


@contextmanager
def ollama_governor():
    """
    Context manager that controls the flow of requests to Ollama.
    1. Respects the 'ollama:max_parallel' setting (Semaphore).
    2. Forces serialization (Lock) if the system is under thermal pressure.
    """
    global _ollama_semaphore, _ollama_limit
    
    from core.settings import settings
    
    # 1. Initialize or update semaphore if limit changed (or first run)
    limit = int(settings.get('ollama:max_parallel') or 1)
    if _ollama_semaphore is None or _ollama_limit != limit:
        # Note: changing limit at runtime might lead to a temporary burst
        # as the old semaphore is discarded, but it's safe.
        _ollama_semaphore = threading.Semaphore(limit) if limit > 0 else None
        _ollama_limit = limit

    # 2. Acquire parallel slot
    if _ollama_semaphore:
        with _ollama_semaphore:
            # 3. Check for thermal pressure (Governor Action)
            state = get_throttle_state()
            if state in ('throttled', 'cooldown'):
                # Force serialization under pressure
                with _ollama_serial_lock:
                    yield
            else:
                yield
    else:
        # No parallel limit. Still serialize under pressure.
        state = get_throttle_state()
        if state in ('throttled', 'cooldown'):
            with _ollama_serial_lock:
                yield
        else:
            yield


def get_last_reading() -> Optional[MonitorReading]:
    with _throttle_lock:
        return _last_reading


def _set_throttle_state(state: str, reading: MonitorReading, reason: str = ''):
    global _throttle_state, _throttle_reason, _last_reading
    with _throttle_lock:
        _throttle_state = state
        _throttle_reason = reason
        _last_reading   = reading


def _load_thresholds() -> ThrottleThresholds:
    """Read thresholds from settings at call time (not import time)."""
    try:
        from core.settings import settings as s
        return ThrottleThresholds(
            gpu_temp_throttle = float(s.get('monitor:gpu_temp_throttle')  or 80),
            gpu_temp_cooldown = float(s.get('monitor:gpu_temp_cooldown')  or 88),
            gpu_util_throttle = float(s.get('monitor:gpu_util_throttle')  or 70),
            sustained_minutes = int(  s.get('monitor:sustained_minutes')  or 3),
            cooldown_minutes  = int(  s.get('monitor:cooldown_minutes')   or 10),
            ram_throttle_pct  = float(s.get('monitor:ram_throttle_pct')   or 85),
            cpu_temp_throttle = float(s.get('monitor:cpu_temp_throttle')  or 85),
        )
    except Exception:
        return ThrottleThresholds()


def _record_sample(reading: MonitorReading, state: str):
    """Write sample to logs.db system_stats. Best-effort."""
    try:
        from core.manager import get_logs_db_path, _connect
        with _connect(get_logs_db_path()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO system_stats
                   (sampled_at, cpu_pct, cpu_temp, ram_used_gb, ram_total_gb, ram_pct,
                    gpu_temp, gpu_util_pct, vram_used_gb, vram_total_gb, 
                    disk_free_gb, disk_free_pct, throttle_state)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (reading.sampled_at, reading.cpu_pct, reading.cpu_temp,
                 reading.ram_used_gb, reading.ram_total_gb, reading.ram_pct,
                 reading.gpu_temp, reading.gpu_util_pct,
                 reading.vram_used_gb, reading.vram_total_gb,
                 reading.disk_free_gb, reading.disk_free_pct, state)
            )
            conn.commit()
    except Exception:
        pass


class HardwareMonitor:
    """
    Daemon that samples hardware sensors on a configurable interval
    and maintains the shared throttle state read by workers.
    """

    def __init__(self):
        self._sm = ThrottleStateMachine()
        self._sensors: list[BaseSensor] = []
        self._init_sensors()

    def _init_sensors(self):
        """Attempt to initialise each sensor. Fall back to dummy on failure."""
        candidates = [PSUtilSensor(), NvidiaSensor(), WMICPUTempSensor(), DiskSensor()]
        for sensor in candidates:
            if sensor.initialise():
                self._sensors.append(sensor)
                logger.info(f"{sensor.name} initialised", ext="monitor")
            else:
                dummy = DummySensor(f"Dummy({sensor.name})")
                dummy.initialise()
                self._sensors.append(dummy)
                logger.warn(f"{sensor.name} unavailable -- using dummy (zeros)", ext="monitor")

    def _sample(self) -> MonitorReading:
        readings = [s.read() for s in self._sensors]
        return _merge_readings(*readings)

    def _get_interval(self) -> int:
        try:
            from core.settings import settings as s
            return int(s.get('monitor:sample_interval') or 60)
        except Exception:
            return 60

    def _is_enabled(self) -> bool:
        try:
            from core.settings import settings as s
            return str(s.get('monitor:enabled') or 'true').lower() != 'false'
        except Exception:
            return True

    def run(self):
        """Main daemon loop. Call from a daemon thread."""
        logger.info("Resource governor starting", ext="monitor")
        while True:
            if self._is_enabled():
                reading    = self._sample()
                thresholds = _load_thresholds()
                self._sm.update(reading, thresholds)
                _set_throttle_state(self._sm.state, reading, self._sm.reason)
                _record_sample(reading, self._sm.state)
            time.sleep(self._get_interval())
