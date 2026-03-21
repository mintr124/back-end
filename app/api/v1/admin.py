from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User

router = APIRouter()

#TODO: do after for refresh phase

def require_admin(user: User):
    if user.role not in {"director", "admin_auditor"}:
        raise HTTPException(status_code=403, detail="Director or admin_auditor privileges required")
    return True


@router.post("/rules/reload")
def reload_rules(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "rules reloaded"}


@router.post("/fga/sync")
def sync_fga(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "fga sync completed"}


@router.post("/reindex")
def reindex(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "reindex queued"}


@router.post("/override-metadata")
def override_metadata(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "override accepted"}
