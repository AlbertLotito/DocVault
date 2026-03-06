from fastapi import APIRouter, Query
from core.monitor import get_throttle_state, get_last_reading, _load_thresholds
from core import manager

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/status")
def monitor_status():
    reading = get_last_reading()
    state, reason = get_throttle_state()
    thresholds = _load_thresholds()
    active_tasks = manager.get_active_tasks(manager.get_db_path())
    
    if reading is None:
        return {
            "state": state, 
            "reason": reason,
            "available": False, 
            "active_tasks": active_tasks,
            "thresholds": thresholds
        }
        
    return {
        "state":         state,
        "reason":        reason,
        "available":     True,
        "cpu_pct":       reading.cpu_pct,
        "cpu_temp":      reading.cpu_temp,
        "ram_used_gb":   reading.ram_used_gb,
        "ram_total_gb":  reading.ram_total_gb,
        "ram_pct":       reading.ram_pct,
        "gpu_temp":      reading.gpu_temp,
        "gpu_util_pct":  reading.gpu_util_pct,
        "vram_used_gb":  reading.vram_used_gb,
        "vram_total_gb": reading.vram_total_gb,
        "disk_free_gb":  reading.disk_free_gb,
        "disk_free_pct": reading.disk_free_pct,
        "sampled_at":    reading.sampled_at,
        "active_tasks":  active_tasks,
        "thresholds":    thresholds
    }


@router.get("/history")
def monitor_history(hours: int = Query(1, ge=1, le=24)):
    """Returns historical system statistics from logs.db."""
    return manager.get_system_stats_history(hours)
