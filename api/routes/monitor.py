import dataclasses
from fastapi import APIRouter, Query
from core.monitor import get_throttle_state, get_last_reading, _load_thresholds, get_embed_stall_state
from core import manager
from core.settings import settings

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/status")
def monitor_status():
    reading = get_last_reading()
    state, reason = get_throttle_state()
    embed_stalled, embed_stall_mins = get_embed_stall_state()
    embed_threshold = int(settings.get('monitor:embed_stall_threshold_mins') or 5)
    active_tasks = manager.get_active_tasks(manager.get_db_path())

    thresholds_dict = dataclasses.asdict(_load_thresholds())
    thresholds_dict['embed_stall_threshold_mins'] = embed_threshold

    if reading is None:
        return {
            "state": state,
            "reason": reason,
            "available": False,
            "active_tasks": active_tasks,
            "thresholds": thresholds_dict,
            "embed_stall": embed_stalled,
            "embed_stall_minutes": embed_stall_mins,
        }

    return {
        "state":               state,
        "reason":              reason,
        "available":           True,
        "cpu_pct":             reading.cpu_pct,
        "cpu_temp":            reading.cpu_temp,
        "ram_used_gb":         reading.ram_used_gb,
        "ram_total_gb":        reading.ram_total_gb,
        "ram_pct":             reading.ram_pct,
        "gpu_temp":            reading.gpu_temp,
        "gpu_util_pct":        reading.gpu_util_pct,
        "vram_used_gb":        reading.vram_used_gb,
        "vram_total_gb":       reading.vram_total_gb,
        "disk_free_gb":        reading.disk_free_gb,
        "disk_free_pct":       reading.disk_free_pct,
        "sampled_at":          reading.sampled_at,
        "active_tasks":        active_tasks,
        "thresholds":          thresholds_dict,
        "embed_stall":         embed_stalled,
        "embed_stall_minutes": embed_stall_mins,
    }


@router.get("/history")
def monitor_history(hours: int = Query(1, ge=1, le=24)):
    """Returns historical system statistics from logs.db."""
    return manager.get_system_stats_history(hours)
