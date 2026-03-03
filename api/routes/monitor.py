from fastapi import APIRouter
from core.monitor import get_throttle_state, get_last_reading

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/status")
def monitor_status():
    reading = get_last_reading()
    state   = get_throttle_state()
    if reading is None:
        return {"state": state, "available": False}
    return {
        "state":         state,
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
        "sampled_at":    reading.sampled_at,
    }
