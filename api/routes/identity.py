from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core import manager

router = APIRouter(prefix="/api/identity", tags=["identity"])

def _db():
    from api.main import DB_PATH
    return DB_PATH

class RenameRequest(BaseModel):
    cluster_id: str
    new_name: str

class MergeRequest(BaseModel):
    target_cluster_id: str
    source_cluster_id: str

@router.get("/registry")
def get_registry():
    """Returns the list of all unique identities in the system."""
    return manager.get_face_registry(_db())

@router.post("/rename")
def rename_person(req: RenameRequest):
    """Update the display name for a specific cluster."""
    manager.rename_person(_db(), req.cluster_id, req.new_name)
    return {"status": "ok"}

@router.get("/faces/{cluster_id}")
def get_person_faces(cluster_id: str):
    """Returns all detections (photos) associated with a person."""
    return manager.get_person_detections(_db(), cluster_id)

@router.post("/merge")
def merge_people(req: MergeRequest):
    """Merge two person clusters into one."""
    manager.merge_people(_db(), req.target_cluster_id, req.source_cluster_id)
    return {"status": "ok"}
