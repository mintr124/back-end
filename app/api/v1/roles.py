from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.deps import get_current_user
from app.models.role import Role
from app.models.clearance_level import ClearanceLevel
from app.models.user import User
from app.schemas.role import RoleResponse
from app.schemas.clearance_level import ClearanceLevelResponse

router = APIRouter()

@router.get("/roles", response_model=list[RoleResponse])
def list_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Role).all()

@router.get("/clearance-levels", response_model=list[ClearanceLevelResponse])
def list_clearance_levels(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(ClearanceLevel).order_by(ClearanceLevel.level).all()