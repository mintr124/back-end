# app/api/v1/departments.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_trace_id  
from app.db.session import get_db
from app.models.user import User
from app.schemas.department import DepartmentCreateRequest, DepartmentResponse
from app.services.department_service import department_service

router = APIRouter()


@router.get("", response_model=list[DepartmentResponse])
def list_departments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return department_service.list_departments(db)


@router.post("", response_model=DepartmentResponse, status_code=201)
def create_department(
    payload: DepartmentCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_trace_id),
):
    return department_service.create_department(db, current_user, payload, trace_id)