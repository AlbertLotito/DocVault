from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.settings import settings

router = APIRouter()


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.get("/settings")
def get_settings():
    return {"settings": settings.get_all_configurable()}


@router.post("/settings")
def update_setting(body: SettingUpdate):
    try:
        settings.set(body.key, body.value)
        return {"status": "ok", "key": body.key, "value": body.value}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
