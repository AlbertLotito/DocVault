from fastapi import APIRouter
from core import manager

router = APIRouter()


def get_db():
    from api.main import DB_PATH
    return DB_PATH


@router.get("/workers/status")
def worker_status():
    return {'paused': manager.get_pause_state()}


@router.post("/workers/pause")
def pause():
    manager.set_pause_state(True)
    return {'paused': True}


@router.post("/workers/resume")
def resume():
    manager.set_pause_state(False)
    return {'paused': False}


@router.post("/workers/reset_stuck")
def reset_stuck():
    count = manager.reset_stuck_tasks(get_db())
    return {'reset': count}
