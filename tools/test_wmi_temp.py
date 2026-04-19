"""
Standalone test for WMI CPU temperature sensor.
Run from the project root: python tools/test_wmi_temp.py
"""

import sys

print("==> Testing MSAcpi_ThermalZoneTemperature via root/wmi namespace")
try:
    import wmi
    w = wmi.WMI(namespace='root/wmi')
    temps = w.MSAcpi_ThermalZoneTemperature()
    if temps:
        for i, t in enumerate(temps):
            celsius = (t.CurrentTemperature / 10.0) - 273.15
            print(f"    Zone {i}: {t.CurrentTemperature} (raw) -> {celsius:.1f} C")
    else:
        print("    [EMPTY] Query succeeded but returned no temperature zones.")
        print("    This hardware/driver does not expose ACPI thermal zones via WMI.")
except Exception as e:
    print(f"    [FAIL] {e}")

print()
print("==> Testing MSAcpi_ThermalZoneTemperature via root/cimv2 namespace (fallback)")
try:
    import wmi
    w = wmi.WMI(namespace='root/cimv2')
    temps = w.query("SELECT * FROM Win32_TemperatureProbe")
    if temps:
        for i, t in enumerate(temps):
            print(f"    Probe {i}: CurrentReading={t.CurrentReading}, Name={t.Name}")
    else:
        print("    [EMPTY] No Win32_TemperatureProbe instances found.")
except Exception as e:
    print(f"    [FAIL] {e}")

print()
print("==> Testing via OpenHardwareMonitor WMI bridge (root/OpenHardwareMonitor)")
print("    (only works if OpenHardwareMonitor is running)")
try:
    import wmi
    w = wmi.WMI(namespace='root/OpenHardwareMonitor')
    sensors = w.Sensor()
    cpu_temps = [s for s in sensors if s.SensorType == 'Temperature' and 'CPU' in (s.Name or '')]
    if cpu_temps:
        for s in cpu_temps:
            print(f"    {s.Name}: {s.Value:.1f} C")
    else:
        print("    [EMPTY] No CPU temperature sensors found via OpenHardwareMonitor.")
except Exception as e:
    print(f"    [FAIL] {e}")

print()
print("==> Testing psutil cpu_freq / sensors_temperatures (cross-platform fallback)")
try:
    import psutil
    if hasattr(psutil, 'sensors_temperatures'):
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                for e in entries:
                    print(f"    [{name}] {e.label or 'n/a'}: {e.current:.1f} C")
        else:
            print("    [EMPTY] psutil.sensors_temperatures() returned nothing (normal on Windows).")
    else:
        print("    [N/A] psutil.sensors_temperatures not available on this platform.")
except Exception as e:
    print(f"    [FAIL] {e}")
